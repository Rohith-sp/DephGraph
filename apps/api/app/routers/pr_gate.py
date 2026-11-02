import hashlib
import hmac
import json
import time
from typing import Annotated, Any

import httpx
import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import settings
from app.database import DbSession
from app.schemas.analysis import VerdictOut
from app.schemas.common import Envelope, ok
from app.services import github, pr_gate_engine
from app.services.queries import lock_service, require_service, rows

log = structlog.get_logger()
router = APIRouter(prefix="/pr-gate", tags=["pr-gate"])
ACTIONS = {"opened", "synchronize", "reopened"}


def _valid_signature(body: bytes, header: str | None) -> bool:
    if not settings.github_webhook_secret or not header:
        return False
    digest = hmac.new(settings.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header.encode(), f"sha256={digest}".encode())


def _skipped(reason: str) -> dict[str, Any]:
    return ok({"skipped": True, "reason": reason})


@router.post("/webhook")
async def webhook(
    request: Request,
    session: DbSession,
    x_github_event: Annotated[str | None, Header()] = None,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
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
    except (ValueError, KeyError, TypeError):
        raise HTTPException(422, "Malformed pull_request payload") from None
    if action not in ACTIONS:
        return ok({"skipped": True})

    html_url = str(repo.get("html_url", "")).rstrip("/").removesuffix(".git")
    async with session.begin():
        svc = await rows(
            session,
            "SELECT service_id FROM service WHERE lower(repo_url) = lower(:url)",
            url=html_url,
        )
    if not svc:
        return _skipped("repository not registered")
    service_id = svc[0]["service_id"]

    install_id = (payload.get("installation") or {}).get("id")
    # no DB transaction is held across GitHub or Groq calls: short read tx, then short write tx
    async with httpx.AsyncClient(timeout=15) as client:
        token = (
            await github.installation_token(client, install_id)
            if github.configured() and install_id
            else None
        )
        deps = await github.pr_added_deps(client, repo["full_name"], number, token)
        async with session.begin():
            results = await pr_gate_engine.evaluate_all(session, deps)
        verdict, stage = await pr_gate_engine.refine(results, service_id, number)
        async with session.begin():
            await lock_service(session, service_id)
            await require_service(session, service_id)  # deleted mid-review -> 404, not FK 500
            await _store(session, service_id, number, pr, results)
        processing_ms = int((time.monotonic() - started) * 1000)
        if token:
            summary = "\n".join(
                f"- {d.name} {d.version}: {d.verdict} ({d.reasoning})" for d in results
            )
            try:
                await github.post_check_run(
                    client,
                    repo["full_name"],
                    head_sha,
                    token,
                    verdict,
                    summary or "No new dependencies.",
                )
            except httpx.HTTPError as e:
                log.warning("check_run_failed", error=str(e))
        else:
            log.info("check_run_skipped", reason="github app not configured")
    return ok(
        {
            "pr_number": number,
            "verdict": verdict,
            "stage_reached": stage,
            "processing_ms": processing_ms,
            "deps": [
                {
                    "name": d.name,
                    "version": d.version,
                    "verdict": d.verdict,
                    "stage_reached": d.stage_reached,
                    "reasoning": d.reasoning,
                }
                for d in results
            ],
        }
    )


def _worst_per_name(results: list[pr_gate_engine.DepVerdict]) -> list[pr_gate_engine.DepVerdict]:
    """The PK is (service, pr, dep_name) but deps differ by ecosystem/version: keep the worst."""
    worst: dict[str, pr_gate_engine.DepVerdict] = {}
    for d in results:
        key = d.name[:255]
        if (
            key not in worst
            or pr_gate_engine.RANK[d.verdict] > pr_gate_engine.RANK[worst[key].verdict]
        ):
            worst[key] = d
    return list(worst.values())


async def _store(
    session: AsyncSession,
    service_id: int,
    number: int,
    pr: dict[str, Any],
    results: list[pr_gate_engine.DepVerdict],
) -> None:
    await session.execute(
        text(
            "INSERT INTO pr_review (service_id, pr_number, pr_url, author) "
            "VALUES (:s, :n, :url, :author) ON CONFLICT (service_id, pr_number) "
            "DO UPDATE SET pr_url = EXCLUDED.pr_url, author = EXCLUDED.author"
        ),
        {
            "s": service_id,
            "n": number,
            "url": str(pr.get("html_url", ""))[:500],
            "author": str((pr.get("user") or {}).get("login", ""))[:120] or None,
        },
    )
    await session.execute(
        text("DELETE FROM pr_risk_verdict WHERE service_id = :s AND pr_number = :n"),
        {"s": service_id, "n": number},
    )
    for d in _worst_per_name(results):
        await session.execute(
            text(
                "INSERT INTO pr_risk_verdict (service_id, pr_number, dep_name, risk_level, "
                "stage_reached, context_tokens, reasoning) "
                "VALUES (:s, :n, :dep, :v, :st, :ct, :why)"
            ),
            {
                "s": service_id,
                "n": number,
                "dep": d.name[:255],
                "v": d.verdict,
                "st": d.stage_reached,
                "ct": d.context_tokens,
                "why": d.reasoning,
            },
        )


@router.get(
    "/verdicts/{service_id}",
    response_model=Envelope[list[VerdictOut]],
    dependencies=[Depends(get_current_user)],
)
async def verdicts(service_id: int, session: DbSession) -> dict[str, Any]:
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
