import hmac
import time
from collections.abc import Awaitable
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.database import DbSession, get_mongo, get_redis
from app.routers import pr_gate
from app.schemas.analysis import SyncOut
from app.schemas.common import Envelope, ok
from app.services import (
    alerts,
    cache,
    cluster_trigger,
    maintainers,
    osv_fetcher,
    pr_gate_queue,
)
from app.services.manifest_parser import PkgRef
from app.services.queries import LATEST, rows

log = structlog.get_logger()


def require_cron_secret(x_cron_secret: Annotated[str | None, Header()] = None) -> None:
    expected = settings.internal_cron_secret
    if not expected or not hmac.compare_digest((x_cron_secret or "").encode(), expected.encode()):
        raise HTTPException(401, "Invalid cron secret")


router = APIRouter(
    prefix="/internal", dependencies=[Depends(require_cron_secret)], tags=["internal"]
)


@router.post("/osv-sync", response_model=Envelope[SyncOut])
async def osv_sync(session: DbSession) -> dict[str, object]:
    """Cron: re-query OSV for every package version in a latest production deployment."""
    async with session.begin():
        found = await rows(
            session,
            f"""
            WITH {LATEST}
            SELECT DISTINCT p.package_id, p.ecosystem, p.name, rt.version_string AS version
            FROM latest l
            JOIN resolves_to rt ON (rt.service_id, rt.deploy_seq) = (l.service_id, l.deploy_seq)
            JOIN package p ON p.package_id = rt.package_id
            """,
        )
    refs = [PkgRef(r["package_id"], r["ecosystem"], r["name"], r["version"]) for r in found]
    hits, advisories = await osv_fetcher.fetch_advisories(refs) if refs else ({}, {})
    await osv_fetcher.store_raw(advisories)  # raw layer first (best-effort on the cron path)
    async with session.begin():
        vulns = await osv_fetcher.upsert_advisories(session, refs, hits, advisories)
    await cache.bump(catalog=True)
    await alerts.run()
    return ok({"packages_scanned": len(refs), "vulns_fetched": vulns})


class KeepaliveOut(BaseModel):
    postgres: bool
    mongo: bool
    redis: bool


@router.post("/keepalive", response_model=Envelope[KeepaliveOut])
async def keepalive(session: DbSession) -> dict[str, object]:
    """Cron: touch Postgres, Atlas and Redis so free tiers do not pause for inactivity.

    503 naming the stores that did not answer, so the scheduled workflow fails and GitHub emails it.
    """

    async def alive(probe: Awaitable[object]) -> bool:
        try:
            await probe
        except (SQLAlchemyError, PyMongoError, RedisError, OSError) as e:
            log.warning("keepalive_failed", error=str(e))
            return False
        return True

    up = {
        "postgres": await alive(session.execute(text("SELECT 1"))),
        "mongo": await alive(get_mongo().command("ping")),
        "redis": await alive(get_redis().ping()),
    }
    if down := [name for name, is_up in up.items() if not is_up]:
        raise HTTPException(503, f"Unreachable: {', '.join(down)}")
    return ok(up)


@router.post("/cluster-recompute", status_code=202)
async def cluster_recompute() -> dict[str, object]:
    """Same as POST /clusters/recompute, cron-secret auth."""
    return ok({"status": await cluster_trigger.trigger()})


class MaintainerSyncOut(BaseModel):
    packages_scanned: int
    packages_with_data: int
    maintainers_upserted: int


@router.post("/maintainers-sync", response_model=Envelope[MaintainerSyncOut])
async def maintainers_sync(session: DbSession) -> dict[str, object]:
    """Cron: refresh maintainers + last activity (npm, PyPI, crates.io) for every package.

    Feeds v_abandonment (migration 007). Go/maven/nuget are skipped (no keyless registry data).
    Registry calls run outside the transaction, bounded by maintainers.CONCURRENCY.
    """
    async with session.begin():
        found = await rows(
            session,
            "SELECT package_id, ecosystem, name FROM package "
            "WHERE ecosystem = ANY(:ecos) ORDER BY package_id",
            ecos=list(maintainers.SUPPORTED),
        )
    pkgs = [(r["package_id"], r["ecosystem"], r["name"]) for r in found]
    fetched = await maintainers.fetch_all(pkgs) if pkgs else {}
    async with session.begin():
        upserted = await maintainers.upsert(session, fetched)
    await cache.bump(catalog=True)
    return ok(
        {
            "packages_scanned": len(pkgs),
            "packages_with_data": sum(1 for m in fetched.values() if m.people),
            "maintainers_upserted": upserted,
        }
    )


RETRY_BUDGET = 200  # seconds: stop claiming new deliveries well before the function's 300 s limit


class PrGateRetryOut(BaseModel):
    processed: int
    failed: int
    purged: int


@router.post("/pr-gate/retry", response_model=Envelope[PrGateRetryOut])
async def pr_gate_retry() -> dict[str, object]:
    """Cron: process stored webhook deliveries (PR reviews, imports) that failed or whose worker
    died, then clean up."""
    deadline = time.monotonic() + RETRY_BUDGET
    processed = failed = 0
    while time.monotonic() < deadline and (claimed := await pr_gate_queue.claim()):
        delivery_id, payload, event = claimed
        try:
            await pr_gate.attempt(delivery_id, payload, event=event)
            processed += 1
        except Exception:  # noqa: BLE001 -- attempt() scheduled the retry; keep draining
            failed += 1
    if time.monotonic() < deadline:  # alerts for manifests ingested outside GitHub imports
        await alerts.run()
    return ok(
        {
            "processed": processed,
            "failed": failed,
            "purged": await pr_gate_queue.purge(),
        }
    )
