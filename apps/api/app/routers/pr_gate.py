import hashlib
import hmac
import json
import time
import uuid
from typing import Annotated, Any

import httpx
import structlog
from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
)

from app.auth import CurrentMember, get_current_user
from app.config import settings
from app.database import DbSession, SessionLocal
from app.schemas.analysis import VerdictOut
from app.schemas.common import BigId, Envelope, ok
from app.services import cache, github, github_import, pr_gate_engine, pr_gate_queue
from app.services.queries import (
    REPO_URL_SQL,
    lock_service,
    normalize_repo_url,
    require_service,
    rows,
)

log = structlog.get_logger()
router = APIRouter(prefix="/pr-gate", tags=["pr-gate"])
ACTIONS = {"opened", "synchronize", "reopened"}


def _valid_signature(body: bytes, header: str | None) -> bool:
    if not settings.github_webhook_secret or not header:
        return False
    digest = hmac.new(settings.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header.encode(), f"sha256={digest}".encode())


def _parse(payload: Any) -> tuple[dict[str, Any], int, str, str, str, str]:
    """(pull_request, number, head sha, action, repo full name, repo html url); ValueError if malformed."""
    try:
        pr, repo = payload["pull_request"], payload["repository"]
        return (
            pr,
            int(pr["number"]),
            pr["head"]["sha"],
            payload["action"],
            repo["full_name"],
            str(repo.get("html_url", "")),
        )
    except (KeyError, TypeError) as e:
        raise ValueError("Malformed pull_request payload") from e


def _failure(e: Exception) -> str:
    return f"DephGraph could not evaluate this PR: {type(e).__name__}"


# Synchronous, as the PRD asks (verdict < 30s). GitHub drops a delivery unanswered after 10s, so it
# talks to the Next.js route (apps/web/src/app/api/webhooks/github), which acknowledges at once and
# forwards here inside `after()`; Python on Vercel cannot keep working after its response. The
# delivery is stored first (pr_gate_queue): a redelivery is a no-op, and a review that fails or
# crashes is retried by POST /internal/pr-gate/retry. push (default branch) and
# installation_repositories deliveries share the queue: they import manifests (github_import).
@router.post("/webhook")
async def webhook(
    request: Request,
    x_github_event: Annotated[str | None, Header()] = None,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
    x_github_delivery: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    started = time.monotonic()
    body = await request.body()  # raw bytes: the HMAC covers exactly what GitHub signed
    if not _valid_signature(body, x_hub_signature_256):
        raise HTTPException(401, "Invalid signature")
    if x_github_event is None or x_github_event not in {
        "pull_request",
        *github_import.EVENTS,
    }:
        return ok({"skipped": True})
    try:
        payload = json.loads(body)
        wanted = (
            _parse(payload)[3] in ACTIONS
            if x_github_event == "pull_request"
            else github_import.wanted(x_github_event, payload)
        )
    except ValueError:  # includes json.JSONDecodeError
        raise HTTPException(422, f"Malformed {x_github_event} payload") from None
    if not wanted:
        return ok({"skipped": True})
    delivery = x_github_delivery or uuid.uuid4().hex
    if not await pr_gate_queue.record(delivery, x_github_event, payload):
        return ok({"duplicate": True})
    try:
        return ok(await attempt(delivery, payload, started, x_github_event))
    except Exception as e:  # noqa: BLE001 -- already recorded for retry; the caller sees a 502
        raise HTTPException(502, _failure(e)) from e


async def attempt(
    delivery_id: str,
    payload: dict[str, Any],
    started: float | None = None,
    event: str = "pull_request",
) -> dict[str, Any]:
    """Handle a claimed delivery (PR review, or a push / App-install import) and record the
    outcome: done, or scheduled for retry (then raises)."""
    try:
        result = (
            await process(payload, delivery_id, started or time.monotonic())
            if event == "pull_request"
            else await github_import.handle(event, payload)
        )
    except Exception as e:
        await pr_gate_queue.fail(delivery_id, e)
        raise
    await pr_gate_queue.done(delivery_id)
    return result


async def process(payload: dict[str, Any], delivery_id: str, started: float) -> dict[str, Any]:
    """Evaluate the PR and post the Check Run (neutral when evaluation fails, then re-raise)."""
    pr, number, head_sha, _, full_name, html_url = _parse(payload)
    install_id = (payload.get("installation") or {}).get("id")
    ctx = {"delivery": delivery_id, "repo": full_name, "pr": number}
    token: str | None = None
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            if github.configured() and install_id:
                token = await github.installation_token(client, install_id)
            verdict = await _evaluate_and_store(client, pr, number, full_name, html_url, token)
            if verdict is None:
                log.info("pr_gate_skipped", reason="repository not registered", **ctx)
                return {"skipped": True}
            overall, stage, summary, deps = verdict
            ms = int((time.monotonic() - started) * 1000)
            log.info("pr_gate_done", verdict=overall, ms=ms, **ctx)
            result = {
                "pr_number": number,
                "verdict": overall,
                "stage_reached": stage,
                "processing_ms": ms,
                "deps": deps,
            }
        except Exception as e:
            log.exception("pr_gate_failed", **ctx)
            overall, summary = "ADVISORY", _failure(e)
            await _post_check_run(client, full_name, head_sha, token, overall, summary, ctx)
            raise
        await _post_check_run(client, full_name, head_sha, token, overall, summary, ctx)
    return result


async def _post_check_run(
    client: httpx.AsyncClient,
    full_name: str,
    head_sha: str,
    token: str | None,
    overall: str,
    summary: str,
    ctx: dict[str, Any],
) -> None:
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
) -> tuple[str, int, str, list[dict[str, Any]]] | None:
    """Review the PR for every service registered for the repo (None when none is):
    (worst verdict, highest stage, Check Run summary, per-dependency results)."""
    async with SessionLocal() as session, session.begin():
        services = await rows(
            session,
            f"SELECT service_id, name, team_id, root_path FROM service WHERE {REPO_URL_SQL} = :url ORDER BY service_id",
            url=normalize_repo_url(html_url),
        )
    if not services:
        return None
    # a monorepo's services each see only the manifests under their root_path ('' = whole repo)
    changes: dict[str, tuple[list[tuple[str, str, str]], list[str]]] = {}
    for svc in services:
        root = svc["root_path"].strip("/")
        if root not in changes:
            changes[root] = await github.pr_added_deps(
                client, full_name, number, token, f"{root}/" if root else ""
            )
    everything: list[pr_gate_engine.DepVerdict] = []
    deps_out: list[dict[str, Any]] = []
    lines: list[str] = []
    for svc in services:
        sid = svc["service_id"]
        deps, notes = changes[svc["root_path"].strip("/")]
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
                await cache.bump([svc["team_id"]])
        everything += results
        deps_out += [
            {
                "service": svc["name"],
                "name": d.name,
                "version": d.version,
                "verdict": d.verdict,
                "stage_reached": d.stage_reached,
                "reasoning": d.reasoning,
            }
            for d in results
        ]
        prefix = f"{svc['name']}: " if len(services) > 1 else ""
        lines += [f"- {prefix}{d.name} {d.version}: {d.verdict} ({d.reasoning})" for d in results]
    worst, stage = pr_gate_engine.overall(everything)
    return worst, stage, "\n".join(lines) or "No new dependencies.", deps_out


@router.get(
    "/verdicts/{service_id}",
    response_model=Envelope[list[VerdictOut]],
    dependencies=[Depends(get_current_user)],
)
async def verdicts(service_id: BigId, session: DbSession, member: CurrentMember) -> dict[str, Any]:
    await require_service(session, service_id, member)
    return ok(
        await rows(
            session,
            "SELECT pr_number, dep_name, risk_level AS verdict, stage_reached, reasoning, "
            "decided_at FROM pr_risk_verdict WHERE service_id = :id "
            "ORDER BY decided_at DESC, pr_number DESC, dep_name",
            id=service_id,
        )
    )
