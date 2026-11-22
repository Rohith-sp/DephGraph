"""Ingest: MongoDB is the raw layer, PostgreSQL the system of record (one-way Mongo -> Postgres).

/manifest stores the upload in `raw_manifests` first, then `_project` parses that document,
writes the raw OSV advisories to `osv_advisories`, and projects both into Postgres in one
transaction. /replay/{manifest_id} re-runs the same projection from a stored document.
"""

import asyncio
import hashlib
import time
from datetime import UTC, datetime
from typing import Any, TypeVar, cast

import httpx
import structlog
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pymongo import ReturnDocument
from pymongo.errors import PyMongoError
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import DbSession
from app.models.mongo import raw_manifests
from app.models.sql import Package, PackageVersion
from app.schemas.common import BigId, Envelope, ok
from app.schemas.ingest import IngestRequest, IngestResult, ScanResult
from app.services import cache, osv_fetcher
from app.services.deps_dev_client import Metadata, apply_metadata, fetch_metadata
from app.services.manifest_parser import ParsedPackage, PkgRef, parse_manifest
from app.services.queries import chunks, latest_seq, lock_service, require_service, rows

log = structlog.get_logger()
T = TypeVar("T")
router = APIRouter(prefix="/ingest", dependencies=[Depends(get_current_user)], tags=["ingest"])


async def _upsert_packages(
    session: AsyncSession, ecosystem: str, parsed: list[ParsedPackage]
) -> tuple[list[PkgRef], int]:
    """Ensure package + package_version rows exist; returns (refs, new version rows)."""
    # sorted inserts: consistent row-lock order across concurrent ingests avoids deadlocks
    names = sorted({p.name for p in parsed})
    ids: dict[str, int] = {}
    for chunk in chunks([{"ecosystem": ecosystem, "name": n} for n in names]):
        stmt = insert(Package).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["ecosystem", "name"], set_={"name": stmt.excluded.name}
        ).returning(Package.name, Package.package_id)
        ids.update((await session.execute(stmt)).all())
    refs = [PkgRef(ids[p.name], ecosystem, p.name, p.version) for p in parsed]
    versions = [
        {"package_id": pid, "version_string": v}
        for pid, v in sorted({(r.package_id, r.version) for r in refs})
    ]
    inserted = 0
    for chunk in chunks(versions):
        ins = (
            insert(PackageVersion)
            .values(chunk)
            .on_conflict_do_nothing()
            .returning(PackageVersion.package_id)
        )
        inserted += len((await session.execute(ins)).all())
    return refs, inserted


async def _record_deployment(
    session: AsyncSession, service_id: int, parsed: list[ParsedPackage], refs: list[PkgRef]
) -> int:
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
    return int(seq)


async def _record_scan(session: AsyncSession, service_id: int) -> None:
    await session.execute(
        text(
            "INSERT INTO scan_run (service_id, completed_at, source) VALUES (:s, now(), 'manual')"
        ),
        {"s": service_id},
    )


def _mongo_down(e: PyMongoError) -> HTTPException:
    log.warning("raw_store_unavailable", error=str(e))
    return HTTPException(503, "Raw manifest store unavailable")


async def _store_manifest(body: IngestRequest) -> dict[str, Any]:
    """Step 1 of ingest: write the raw upload to Mongo `raw_manifests` before anything else.

    One document per (service_id, sha256): re-uploading identical content reuses the document
    (content via $setOnInsert; ecosystem/filename follow the latest upload) and resets it to status `received`. Raises 503 if Mongo
    is unreachable, because Postgres rows may only be projected from what the raw layer holds.
    """
    digest = hashlib.sha256(body.content.encode()).hexdigest()
    now = datetime.now(UTC)
    try:
        doc = await raw_manifests().find_one_and_update(
            {"service_id": body.service_id, "sha256": digest},
            {
                "$setOnInsert": {
                    "service_id": body.service_id,
                    "sha256": digest,
                    "raw_content": body.content,
                    "uploaded_at": now,
                },
                "$set": {
                    "ecosystem": body.ecosystem,  # describe this upload, not the first one
                    "filename": body.filename,
                    "status": "received",
                    "received_at": now,
                    "updated_at": now,
                },
                "$unset": {"error": ""},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as e:
        raise _mongo_down(e) from e
    return cast(dict[str, Any], doc)


async def _mark(manifest_id: ObjectId, status: str, **fields: Any) -> None:
    """Record the projection outcome on the raw document (best-effort: Postgres already decided)."""
    now = datetime.now(UTC)
    update: dict[str, Any] = {"$set": {"status": status, "updated_at": now, **fields}}
    if status == "projected":
        update["$set"]["projected_at"] = now
        update["$unset"] = {"error": ""}
        update["$push"] = {"deploy_seqs": fields["deploy_seq"]}
    try:
        await raw_manifests().update_one({"_id": manifest_id}, update)
    except PyMongoError as e:
        log.warning("manifest_status_update_failed", manifest_id=str(manifest_id), error=str(e))


def _error_text(e: Exception) -> str:
    if isinstance(e, HTTPException):
        return f"{e.status_code}: {e.detail}"
    return f"{type(e).__name__}: {e}"[:1000]


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


async def _project(session: AsyncSession, doc: dict[str, Any]) -> dict[str, Any]:
    """One-way projection of a stored raw manifest (Mongo) into Postgres; the only write path.

    parse doc content -> fetch OSV + deps.dev (no transaction open) -> store raw advisories in
    Mongo -> ONE Postgres transaction (advisory lock, service check, packages, metadata,
    advisories, deployment + resolves_to closure, scan_run). The manifest document ends up
    `projected` (with deploy_seq) on commit or `failed` (with the error) on any exception.
    """
    started = time.perf_counter()
    service_id, ecosystem = doc["service_id"], doc["ecosystem"]
    try:
        try:
            parsed = parse_manifest(ecosystem, doc["raw_content"])
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        if not parsed:
            raise HTTPException(422, "No versioned packages found in manifest")
        hits, advisories, meta = await _fetch_upstream(
            [PkgRef(0, ecosystem, p.name, p.version) for p in parsed]
        )
        try:  # raw advisories land in Mongo before Postgres derives vulnerability rows
            await osv_fetcher.store_raw(advisories, required=True)
        except PyMongoError as e:
            raise _mongo_down(e) from e
        async with session.begin():
            await lock_service(session, service_id)
            await require_service(session, service_id)  # deleted since the first check -> 404
            refs, inserted = await _upsert_packages(session, ecosystem, parsed)
            await apply_metadata(session, _rekey(refs, meta))
            vulns = await osv_fetcher.upsert_advisories(
                session, refs, _rekey(refs, hits), advisories
            )
            seq = await _record_deployment(session, service_id, parsed, refs)
            await _record_scan(session, service_id)
    except Exception as e:
        await _mark(doc["_id"], "failed", error=_error_text(e))
        raise
    await _mark(doc["_id"], "projected", deploy_seq=seq, parsed_packages=[vars(p) for p in parsed])
    await cache.bump()
    return {
        "manifest_id": str(doc["_id"]),
        "deploy_seq": seq,
        "packages_parsed": len(parsed),
        "packages_inserted": inserted,
        "vulns_fetched": vulns,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }


@router.post("/manifest", response_model=Envelope[IngestResult])
async def ingest_manifest(body: IngestRequest, session: DbSession) -> dict[str, object]:
    """Store the raw manifest in Mongo (status `received`), then project it into Postgres.

    Mongo is the raw layer and the source of the projection, so if the raw document (or the
    raw advisories) cannot be written the request fails with 503 and Postgres is not touched.
    """
    async with session.begin():
        await require_service(session, body.service_id)
    doc = await _store_manifest(body)
    return ok(await _project(session, doc))


@router.post("/replay/{manifest_id}", response_model=Envelope[IngestResult])
async def replay_manifest(manifest_id: str, session: DbSession) -> dict[str, object]:
    """Re-project a stored raw manifest from Mongo into Postgres as a new deployment.

    Demonstrates the one-way flow: Postgres is rebuilt from what Mongo holds, never the reverse.
    422 for a malformed id, 404 if no such document (or its service is gone), 503 if Mongo is
    unreachable.
    """
    if not ObjectId.is_valid(manifest_id):
        raise HTTPException(422, "Invalid manifest id")
    try:
        doc = await raw_manifests().find_one({"_id": ObjectId(manifest_id)})
    except PyMongoError as e:
        raise _mongo_down(e) from e
    if doc is None:
        raise HTTPException(404, "Manifest not found")
    async with session.begin():
        await require_service(session, doc["service_id"])
    return ok(await _project(session, doc))


@router.post("/scan/{service_id}", response_model=Envelope[ScanResult])
async def scan_service(service_id: BigId, session: DbSession) -> dict[str, object]:
    """Re-query OSV for the latest deployment; raw advisories go to Mongo before Postgres.

    Unlike ingest, a Mongo outage does not fail the scan (store_raw is best-effort here).
    """
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
    # raw layer first; best-effort here: a rescan only refreshes advisories for an existing
    # deployment, so a Mongo outage degrades to a Postgres-only refresh instead of failing.
    await osv_fetcher.store_raw(advisories)
    async with session.begin():
        await lock_service(session, service_id)
        await require_service(session, service_id)
        if await latest_seq(session, service_id) != seq:  # a manifest landed mid-scan
            raise HTTPException(409, "Service changed during scan; retry")
        vulns = await osv_fetcher.upsert_advisories(session, refs, hits, advisories)
        await _record_scan(session, service_id)
    await cache.bump()
    return ok(
        {
            "packages_scanned": len(refs),
            "vulns_fetched": vulns,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    )
