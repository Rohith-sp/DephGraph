import hashlib
import hmac
import json
import re
import time
from typing import Annotated, Any

import httpx
import structlog
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
)

from app.auth import get_current_user
from app.config import settings
from app.database import DbSession, SessionLocal
from app.schemas.analysis import VerdictOut
from app.schemas.common import BigId, Envelope, ok
from app.services import cache, github, pr_gate_engine
from app.services.queries import lock_service, require_service, rows

log = structlog.get_logger()
router = APIRouter(prefix="/pr-gate", tags=["pr-gate"])
ACTIONS = {"opened", "synchronize", "reopened"}


def _valid_signature(body: bytes, header: str | None) -> bool:
    if not settings.github_webhook_secret or not header:
        return False
    digest = hmac.new(settings.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header.encode(), f"sha256={digest}".encode())


_REPO_URL_TAIL = r"(\.git)?/*$"
# SQL twin of normalize_repo_url (same rules, so stored and payload URLs meet in the middle)
_REPO_URL_SQL = (
    "regexp_replace(regexp_replace(lower(btrim(repo_url)), '^http://', 'https://'), "
    f"'{_REPO_URL_TAIL}', '')"
)


def normalize_repo_url(url: str) -> str:
    """Lowercase, https, no trailing '/' or '.git': https://GitHub.com/Acme/X.git/ -> .../acme/x"""
    return re.sub(_REPO_URL_TAIL, "", re.sub(r"^http://", "https://", url.strip().lower()))


# PRD §8 asks for a synchronous verdict "under 30s"; GitHub drops a delivery that has not been
# answered within 10s, so the webhook acknowledges with 202 and the review runs as a background
# task. Results are read from GET /pr-gate/verdicts/{service_id} and posted as a Check Run.
@router.post("/webhook")
async def webhook(
    request: Request,
    response: Response,
    tasks: BackgroundTasks,
    x_github_event: Annotated[str | None, Header()] = None,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
    x_github_delivery: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    started = time.monotonic()
    body = await request.body()  # raw bytes: the HMAC covers exactly what GitHub signed
    if not _valid_signature(body, x_hub_signature_256):
        raise HTTPException(401, "Invalid signature")
    if x_github_event != "pull_request":
        return ok({"skipped": True})
    try:
        payload = json.loads(body)
        pr, repo = payload["pull_request"], payload["repository"]
        number, head_sha = int(pr["number"]), pr["head"]["sha"]
        action = payload["action"]
        full_name, html_url = repo["full_name"], str(repo.get("html_url", ""))
    except (ValueError, KeyError, TypeError):
        raise HTTPException(422, "Malformed pull_request payload") from None
    if action not in ACTIONS:
        return ok({"skipped": True})
    install_id = (payload.get("installation") or {}).get("id")
    tasks.add_task(
        _review, pr, number, head_sha, full_name, html_url, install_id, started, x_github_delivery
    )
    response.status_code = 202
    return ok({"accepted": True, "delivery_id": x_github_delivery})


async def _review(
    pr: dict[str, Any],
    number: int,
    head_sha: str,
    full_name: str,
    html_url: str,
    install_id: int | None,
    started: float,
    delivery_id: str | None,
) -> None:
    """Background job with its own sessions; any failure is logged and surfaced as a Check Run."""
    token: str | None = None
    ctx = {"delivery": delivery_id, "repo": full_name, "pr": number}
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            if github.configured() and install_id:
                token = await github.installation_token(client, install_id)
            verdict = await _evaluate_and_store(client, pr, number, full_name, html_url, token)
            if verdict is None:
                log.info("pr_gate_skipped", reason="repository not registered", **ctx)
                return
            overall, summary = verdict
            log.info(
                "pr_gate_done", verdict=overall, ms=int((time.monotonic() - started) * 1000), **ctx
            )
        except Exception as e:  # noqa: BLE001 -- background job: log, never lose silently
            log.exception("pr_gate_failed", **ctx)
            overall, summary = (
                "ADVISORY",
                f"DephGraph could not evaluate this PR: {type(e).__name__}",
            )
        if not token:
            log.info("check_run_skipped", reason="no installation token", **ctx)
            return
        try:
            await github.post_check_run(client, full_name, head_sha, token, overall, summary)
        except httpx.HTTPError as e:
            log.warning("check_run_failed", error=str(e), **ctx)


async def _evaluate_and_store(
    client: httpx.AsyncClient,
    pr: dict[str, Any],
    number: int,
    full_name: str,
    html_url: str,
    token: str | None,
) -> tuple[str, str] | None:
    """Review the PR for every service registered for the repo; (worst verdict, summary)."""
    async with SessionLocal() as session, session.begin():
        services = await rows(
            session,
            f"SELECT service_id, name FROM service WHERE {_REPO_URL_SQL} = :url ORDER BY service_id",
            url=normalize_repo_url(html_url),
        )
    if not services:
        return None
    deps, notes = await github.pr_added_deps(client, full_name, number, token)
    worst, lines = "PASS", []
    for svc in services:
        sid = svc["service_id"]
        # no DB transaction is held across Groq calls: short read tx, then short write tx
        async with SessionLocal() as session:
            async with session.begin():
                results = await pr_gate_engine.evaluate_all(session, deps, sid)
            await pr_gate_engine.refine(results, sid, number)
            # fail closed: manifests we could not read make the PR at least ADVISORY
            results += [
                pr_gate_engine.DepVerdict(f"[not inspected] {n}"[:255], "", "ADVISORY", 2, n)
                for n in notes
            ]
            async with session.begin():
                await lock_service(session, sid)
                if not await rows(
                    session, "SELECT 1 AS ok FROM service WHERE service_id = :s", s=sid
                ):
                    continue  # deleted mid-review
                stored = await pr_gate_engine.store_verdicts(session, sid, number, pr, results)
            if stored:
                await cache.bump()
        verdict, _ = pr_gate_engine.overall(results)
        worst = max(worst, verdict, key=pr_gate_engine.RANK.__getitem__)
        prefix = f"{svc['name']}: " if len(services) > 1 else ""
        lines += [f"- {prefix}{d.name} {d.version}: {d.verdict} ({d.reasoning})" for d in results]
    return worst, "\n".join(lines) or "No new dependencies."


@router.get(
    "/verdicts/{service_id}",
    response_model=Envelope[list[VerdictOut]],
    dependencies=[Depends(get_current_user)],
)
async def verdicts(service_id: BigId, session: DbSession) -> dict[str, Any]:
    await require_service(session, service_id)
    return ok(
        await rows(
            session,
            "SELECT pr_number, dep_name, risk_level AS verdict, stage_reached, reasoning, "
            "decided_at FROM pr_risk_verdict WHERE service_id = :id "
            "ORDER BY decided_at DESC, pr_number DESC, dep_name",
            id=service_id,
        )
    )
