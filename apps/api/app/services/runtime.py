"""Runtime liveness collector: raw samples land in MongoDB, then are projected into `observes`.

One batch = one observation window = one `scan_run` (source 'cli'). Each matched package version
gets an `observes` row with `is_live = exec_count > 0`; `v_prunable` and `prunable_rows` then list
resolved packages that were never live in any window.
"""

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import structlog
from bson import ObjectId
from fastapi import HTTPException
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import PyMongoError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Member
from app.database import get_mongo
from app.schemas.runtime import RuntimeSamplesRequest
from app.services import cache
from app.services.manifest_parser import normalize_pypi
from app.services.queries import latest_seq, lock_service, require_service, rows

log = structlog.get_logger()

INT_MAX = 2_147_483_647
MAX_SKIPPED_LISTED = 50
Key = tuple[str, str]  # (package name, version)


def runtime_samples() -> AsyncCollection:
    return get_mongo()["runtime_samples"]


async def _resolve_seq(
    session: AsyncSession, service_id: int, requested: int | None, member: Member | None
) -> tuple[int, int]:
    """(deploy_seq, team_id) the samples belong to: 404 unknown service/deployment, 409 none at all.

    Without an explicit `requested` seq, samples go to the latest production deployment.
    """
    team_id = (await require_service(session, service_id, member))["team_id"]
    if requested is None:
        seq = await latest_seq(session, service_id)
        if seq is None:
            raise HTTPException(
                409, "Service has no production deployment; ingest a manifest first"
            )
        return seq, team_id
    found = await rows(
        session,
        "SELECT 1 FROM deployment WHERE service_id = :s AND deploy_seq = :q",
        s=service_id,
        q=requested,
    )
    if not found:
        raise HTTPException(404, "Deployment not found")
    return requested, team_id


def _aggregate(body: RuntimeSamplesRequest) -> dict[Key, int]:
    """Duplicate (name, version) samples in one batch are summed (capped at the INT column)."""
    totals: dict[Key, int] = defaultdict(int)
    for s in body.samples:
        totals[(s.name, s.version)] = min(INT_MAX, totals[(s.name, s.version)] + s.exec_count)
    return totals


async def _store_raw(body: RuntimeSamplesRequest, seq: int, captured: datetime) -> ObjectId | None:
    """Raw layer first. Best effort: on a Mongo failure log and return None (see record_samples)."""
    doc = {
        "service_id": body.service_id,
        "deploy_seq": seq,
        "captured_at": captured,
        "received_at": datetime.now(UTC),
        "complete": body.complete,
        "samples": [s.model_dump() for s in body.samples],
        "status": "pending",
        "scan_id": None,
    }
    try:
        return (await runtime_samples().insert_one(doc)).inserted_id
    except PyMongoError as e:
        log.warning("runtime_samples_store_failed", service_id=body.service_id, error=str(e))
        return None


async def _mark_raw(doc_id: ObjectId | None, fields: dict[str, Any]) -> None:
    if doc_id is None:
        return
    try:
        await runtime_samples().update_one({"_id": doc_id}, {"$set": fields})
    except PyMongoError as e:
        log.warning("runtime_samples_mark_failed", doc_id=str(doc_id), error=str(e))


async def _project(
    session: AsyncSession,
    service_id: int,
    seq: int,
    captured: datetime,
    totals: dict[Key, int],
    complete: bool,
) -> tuple[int, list[dict[str, Any]], set[Key]]:
    """In the caller's transaction: scan_run + observes rows. Returns (scan_id, rows, matched)."""
    await lock_service(session, service_id)
    await _resolve_seq(session, service_id, seq, None)  # deleted since the first check -> 404
    resolved = await rows(
        session,
        "SELECT p.name, p.ecosystem, rt.package_id, rt.version_string FROM resolves_to rt "
        "JOIN package p ON p.package_id = rt.package_id "
        "WHERE rt.service_id = :s AND rt.deploy_seq = :q "
        "ORDER BY rt.package_id, rt.version_string",  # stable lock order for the inserts
        s=service_id,
        q=seq,
    )
    scan_id = (
        await session.execute(
            text(
                "INSERT INTO scan_run (service_id, started_at, completed_at, source) "
                "VALUES (:s, least(CAST(:cap AS TIMESTAMPTZ), now()), now(), 'cli') "
                "RETURNING scan_id"
            ),
            {"s": service_id, "cap": captured},
        )
    ).scalar_one()
    out: list[dict[str, Any]] = []
    matched: set[Key] = set()
    # pypi names are stored PEP 503-normalized; agents report them as installed ("Flask")
    by_norm: defaultdict[Key, list[Key]] = defaultdict(list)
    for k in totals:
        by_norm[(normalize_pypi(k[0]), k[1])].append(k)
    for r in resolved:
        key = (r["name"], r["version_string"])
        hit = by_norm[key] if r["ecosystem"] == "pypi" else [key] * (key in totals)
        if hit:
            matched.update(hit)
        elif not complete:
            continue
        n = min(INT_MAX, sum(totals[k] for k in hit))
        out.append(
            {
                "s": service_id,
                "q": seq,
                "scan": scan_id,
                "p": r["package_id"],
                "v": r["version_string"],
                "n": n,
                "live": n > 0,
            }
        )
    if out:
        await session.execute(
            text(
                "INSERT INTO observes (service_id, deploy_seq, scan_id, package_id, "
                "version_string, exec_count, is_live) VALUES (:s, :q, :scan, :p, :v, :n, :live) "
                "ON CONFLICT (service_id, deploy_seq, scan_id, package_id, version_string) "
                "DO UPDATE SET exec_count = EXCLUDED.exec_count, is_live = EXCLUDED.is_live"
            ),
            out,
        )
    return scan_id, out, matched


async def record_samples(
    session: AsyncSession, body: RuntimeSamplesRequest, member: Member
) -> dict[str, Any]:
    """MongoDB first, then one Postgres transaction, then the Mongo doc is marked projected.

    Mongo down: the raw copy is skipped with a warning and the batch is still projected
    (`raw_stored: false`). Postgres is the system of record for liveness and the agent would
    otherwise lose the window; this matches how /ingest treats its raw manifest copy.
    A rejected batch (404/409 after the raw insert) stays in Mongo with status 'rejected'.
    """
    async with session.begin():  # fast fail before writing anything
        seq, team_id = await _resolve_seq(session, body.service_id, body.deploy_seq, member)
    captured = body.captured_at or datetime.now(UTC)
    totals = _aggregate(body)
    doc_id = await _store_raw(body, seq, captured)
    try:
        async with session.begin():
            scan_id, written, matched = await _project(
                session, body.service_id, seq, captured, totals, body.complete
            )
    except HTTPException as e:
        await _mark_raw(doc_id, {"status": "rejected", "error": str(e.detail)})
        raise
    except Exception as e:  # DB error: do not leave the raw doc `pending` forever
        await _mark_raw(doc_id, {"status": "failed", "error": f"{type(e).__name__}: {e}"[:1000]})
        raise
    await cache.bump([team_id])
    skipped = [s for s in body.samples if (s.name, s.version) not in matched]
    distinct_skipped = list(dict.fromkeys((s.name, s.version) for s in skipped))
    await _mark_raw(
        doc_id, {"status": "projected", "scan_id": scan_id, "projected_at": datetime.now(UTC)}
    )
    log.info(
        "runtime_samples_projected",
        service_id=body.service_id,
        deploy_seq=seq,
        scan_id=scan_id,
        written=len(written),
        skipped=len(skipped),
    )
    return {
        "scan_id": scan_id,
        "deploy_seq": seq,
        "samples_received": len(body.samples),
        "observes_written": len(written),
        "live_packages": sum(1 for w in written if w["live"]),
        "samples_skipped": len(skipped),
        "skipped": [{"name": n, "version": v} for n, v in distinct_skipped[:MAX_SKIPPED_LISTED]],
        "raw_stored": doc_id is not None,
    }
