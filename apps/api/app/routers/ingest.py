import asyncio
import hashlib
import time
from datetime import datetime
from typing import Any, TypeVar

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException
from pymongo.errors import PyMongoError
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import DbSession
from app.models.mongo import raw_manifests
from app.models.sql import Package, PackageVersion
from app.schemas.common import Envelope, ok
from app.schemas.ingest import IngestRequest, IngestResult, ScanResult
from app.services import blast_radius, osv_fetcher
from app.services.deps_dev_client import Metadata, apply_metadata, fetch_metadata
from app.services.manifest_parser import ParsedPackage, PkgRef, parse_manifest
from app.services.queries import latest_seq, lock_service, require_service, rows

log = structlog.get_logger()
T = TypeVar("T")
router = APIRouter(prefix="/ingest", dependencies=[Depends(get_current_user)], tags=["ingest"])


async def _upsert_packages(
    session: AsyncSession, ecosystem: str, parsed: list[ParsedPackage]
) -> tuple[list[PkgRef], int]:
    """Ensure package + package_version rows exist; returns (refs, new version rows)."""
    # sorted inserts: consistent row-lock order across concurrent ingests avoids deadlocks
    names = sorted({p.name for p in parsed})
    stmt = insert(Package).values([{"ecosystem": ecosystem, "name": n} for n in names])
    stmt = stmt.on_conflict_do_update(
        index_elements=["ecosystem", "name"], set_={"name": stmt.excluded.name}
    ).returning(Package.name, Package.package_id)
    ids = {name: pid for name, pid in (await session.execute(stmt)).all()}
    refs = [PkgRef(ids[p.name], ecosystem, p.name, p.version) for p in parsed]
    ins = (
        insert(PackageVersion)
        .values(
            [
                {"package_id": pid, "version_string": v}
                for pid, v in sorted({(r.package_id, r.version) for r in refs})
            ]
        )
        .on_conflict_do_nothing()
        .returning(PackageVersion.package_id)
    )
    return refs, len((await session.execute(ins)).all())


async def _record_deployment(
    session: AsyncSession, service_id: int, parsed: list[ParsedPackage], refs: list[PkgRef]
) -> None:
    """New deployment + resolves_to: direct deps, then closure over known depends_on edges."""
    seq = (
        await session.execute(
            text(
                "INSERT INTO deployment (service_id, deploy_seq) "
                "SELECT :s, COALESCE(MAX(deploy_seq), 0) + 1 FROM deployment WHERE service_id = :s "
                "RETURNING deploy_seq"
            ),
            {"s": service_id},
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO resolves_to (service_id, deploy_seq, package_id, version_string, depth, "
            "is_direct) VALUES (:s, :q, :p, :v, :d, :i)"
        ),
        [
            {
                "s": service_id,
                "q": seq,
                "p": r.package_id,
                "v": r.version,
                "d": 0 if p.is_direct else 1,
                "i": p.is_direct,
            }
            for p, r in zip(parsed, refs, strict=True)
        ],
    )
    await session.execute(
        text("""
            INSERT INTO resolves_to (service_id, deploy_seq, package_id, version_string, depth,
                                     is_direct)
            WITH RECURSIVE c AS (
              SELECT package_id, version_string, depth FROM resolves_to
              WHERE service_id = :s AND deploy_seq = :q
              UNION
              SELECT d.ref_package_id, d.ref_version_string, c.depth + 1
              FROM depends_on d
              JOIN c ON (d.dep_package_id, d.dep_version_string) = (c.package_id, c.version_string)
              WHERE c.depth < 10)
            SELECT :s, :q, package_id, version_string, MIN(depth), FALSE FROM c
            GROUP BY package_id, version_string
            ON CONFLICT DO NOTHING
            """),
        {"s": service_id, "q": seq},
    )


async def _record_scan(session: AsyncSession, service_id: int) -> None:
    await session.execute(
        text(
            "INSERT INTO scan_run (service_id, completed_at, source) VALUES (:s, now(), 'manual')"
        ),
        {"s": service_id},
    )


async def _store_manifest(body: IngestRequest, parsed: list[ParsedPackage]) -> None:
    """Best-effort raw copy to Mongo, deduplicated per service by content sha256."""
    digest = hashlib.sha256(body.content.encode()).hexdigest()
    doc = {
        "service_id": body.service_id,
        "ecosystem": body.ecosystem,
        "filename": body.filename,
        "raw_content": body.content,
        "parsed_packages": [vars(p) for p in parsed],
        "uploaded_at": datetime.now().astimezone(),
        "sha256": digest,
    }
    try:
        await raw_manifests().update_one(
            {"service_id": body.service_id, "sha256": digest}, {"$setOnInsert": doc}, upsert=True
        )
    except PyMongoError as e:
        log.warning("manifest_store_failed", error=str(e))


async def _fetch_upstream(
    refs: list[PkgRef],
) -> tuple[dict[PkgRef, list[str]], dict[str, dict[str, Any]], dict[PkgRef, Metadata]]:
    """OSV + deps.dev in parallel with no DB connection held; an OSV failure cancels deps.dev."""
    try:
        async with asyncio.TaskGroup() as tg:
            osv = tg.create_task(osv_fetcher.fetch_advisories(refs))
            meta = tg.create_task(fetch_metadata(refs))
    except* httpx.HTTPError as eg:  # let the app-level 502 handler see the bare error
        raise eg.exceptions[0] from None
    hits, advisories = osv.result()
    return hits, advisories, meta.result()


def _rekey(real: list[PkgRef], data: dict[PkgRef, T]) -> dict[PkgRef, T]:
    """Fetch results are keyed by placeholder refs (package_id 0); re-key to the DB-backed refs."""
    by_name = {(r.name, r.version): r for r in real}
    return {by_name[(k.name, k.version)]: v for k, v in data.items()}


@router.post("/manifest", response_model=Envelope[IngestResult])
async def ingest_manifest(body: IngestRequest, session: DbSession) -> dict[str, object]:
    started = time.perf_counter()
    async with session.begin():
        await require_service(session, body.service_id)
    try:
        parsed = parse_manifest(body.ecosystem, body.content)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if not parsed:
        raise HTTPException(422, "No versioned packages found in manifest")
    hits, advisories, meta = await _fetch_upstream(
        [PkgRef(0, body.ecosystem, p.name, p.version) for p in parsed]
    )
    async with session.begin():
        await lock_service(session, body.service_id)
        await require_service(session, body.service_id)  # deleted since the first check -> 404
        refs, inserted = await _upsert_packages(session, body.ecosystem, parsed)
        await apply_metadata(session, _rekey(refs, meta))
        vulns = await osv_fetcher.upsert_advisories(session, _rekey(refs, hits), advisories)
        await _record_deployment(session, body.service_id, parsed, refs)
        await _record_scan(session, body.service_id)
    await osv_fetcher.store_raw(advisories)
    await _store_manifest(body, parsed)
    await blast_radius.invalidate()
    return ok(
        {
            "packages_parsed": len(parsed),
            "packages_inserted": inserted,
            "vulns_fetched": vulns,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    )


@router.post("/scan/{service_id}", response_model=Envelope[ScanResult])
async def scan_service(service_id: int, session: DbSession) -> dict[str, object]:
    started = time.perf_counter()
    async with session.begin():
        await require_service(session, service_id)
        seq = await latest_seq(session, service_id)
        found = (
            await rows(
                session,
                "SELECT p.package_id, p.ecosystem, p.name, rt.version_string AS version "
                "FROM resolves_to rt JOIN package p ON p.package_id = rt.package_id "
                "WHERE rt.service_id = :s AND rt.deploy_seq = :q",
                s=service_id,
                q=seq,
            )
            if seq is not None
            else []
        )
    refs = [PkgRef(r["package_id"], r["ecosystem"], r["name"], r["version"]) for r in found]
    hits, advisories = await osv_fetcher.fetch_advisories(refs) if refs else ({}, {})
    async with session.begin():
        await lock_service(session, service_id)
        await require_service(session, service_id)
        if await latest_seq(session, service_id) != seq:  # a manifest landed mid-scan
            raise HTTPException(409, "Service changed during scan; retry")
        vulns = await osv_fetcher.upsert_advisories(session, hits, advisories)
        await _record_scan(session, service_id)
    await osv_fetcher.store_raw(advisories)
    await blast_radius.invalidate()
    return ok(
        {
            "packages_scanned": len(refs),
            "vulns_fetched": vulns,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    )
