"""Ingest: MongoDB is the raw layer, PostgreSQL the system of record (one-way Mongo -> Postgres).

/manifest stores the upload in `raw_manifests` first, then `_project_many` parses that document,
writes the raw OSV advisories to `osv_advisories`, and projects both into Postgres in one
transaction. /replay/{manifest_id} re-runs the same projection from a stored document.
"""

import asyncio
import hashlib
import time
from collections import deque
from dataclasses import dataclass, field
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

from app.auth import CurrentMember, get_current_user
from app.database import DbSession
from app.models.mongo import raw_manifests
from app.models.sql import DeploymentEdge, Package, PackageVersion
from app.schemas.common import BigId, Envelope, ok
from app.schemas.ingest import IngestRequest, IngestResult, ScanResult
from app.services import cache, osv_fetcher
from app.services.deps_dev_client import Metadata, apply_metadata, fetch_metadata
from app.services.manifest_parser import (
    ParsedPackage,
    PkgRef,
    is_lockfile,
    parse_manifest,
)
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


async def _record_edges(
    session: AsyncSession,
    service_id: int,
    seq: int,
    parsed: list[ParsedPackage],
    refs: list[PkgRef],
) -> None:
    """The lockfile's edges, stored with this deployment only (never in the global depends_on)."""
    ids = {(r.name, r.version): r.package_id for r in refs}
    edges = sorted(
        {
            (ids[p.name, p.version], p.version, ids[d], d[1])
            for p in parsed
            for d in p.deps
            if d in ids
        }
    )
    for chunk in chunks(edges):
        await session.execute(
            insert(DeploymentEdge).values(
                [
                    {
                        "service_id": service_id,
                        "deploy_seq": seq,
                        "dep_package_id": a,
                        "dep_version_string": av,
                        "ref_package_id": b,
                        "ref_version_string": bv,
                    }
                    for a, av, b, bv in chunk
                ]
            )
        )


def _lock_depths(parsed: list[ParsedPackage]) -> dict[tuple[str, str], int]:
    """Shallowest depth of each package from the direct ones; unreachable ones count as 1."""
    deps = {(p.name, p.version): p.deps for p in parsed}
    depth = {k: 0 for k, p in zip(deps, parsed, strict=True) if p.is_direct}
    queue = deque(depth)
    while queue:
        cur = queue.popleft()
        for nxt in deps[cur]:
            if nxt in deps and nxt not in depth:
                depth[nxt] = depth[cur] + 1
                queue.append(nxt)
    return {k: depth.get(k, 1) for k in deps}


@dataclass
class _Group:
    """One ecosystem's packages in a deployment, from a lockfile or from manifests."""

    ecosystem: str
    parsed: list[ParsedPackage]
    locked: bool
    refs: list[PkgRef] = field(default_factory=list)


async def _record_deployment(
    session: AsyncSession, service_id: int, environment: str, groups: list[_Group]
) -> int:
    """New deployment + resolves_to: each group's packages, then (manifest groups only) the
    closure over known depends_on edges.

    A lockfile already lists every resolved package, so its group takes exactly that set (depth by
    BFS over its own edges, edges kept with this deployment) and never the global depends_on
    closure, which mixes in versions other projects resolved. The deployment is `locked` only when
    every group came from a lockfile.
    """
    seq = (
        await session.execute(
            text(
                "INSERT INTO deployment (service_id, deploy_seq, environment, locked) "
                "SELECT :s, COALESCE(MAX(deploy_seq), 0) + 1, :env, :locked FROM deployment "
                "WHERE service_id = :s RETURNING deploy_seq"
            ),
            {"s": service_id, "env": environment, "locked": all(g.locked for g in groups)},
        )
    ).scalar_one()
    values = []
    for g in groups:
        depth = (
            _lock_depths(g.parsed)
            if g.locked
            else {(p.name, p.version): int(not p.is_direct) for p in g.parsed}
        )
        values += [
            {
                "s": service_id,
                "q": seq,
                "p": r.package_id,
                "v": r.version,
                "d": depth.get((p.name, p.version), 1),
                "i": p.is_direct,
            }
            for p, r in zip(g.parsed, g.refs, strict=True)
        ]
    await session.execute(
        text(
            "INSERT INTO resolves_to (service_id, deploy_seq, package_id, version_string, depth, "
            "is_direct) VALUES (:s, :q, :p, :v, :d, :i)"
        ),
        values,
    )
    for g in groups:
        if g.locked:
            await _record_edges(session, service_id, seq, g.parsed, g.refs)
    manifest_ecosystems = [g.ecosystem for g in groups if not g.locked]
    if manifest_ecosystems:
        await session.execute(
            text("""
                INSERT INTO resolves_to (service_id, deploy_seq, package_id, version_string, depth,
                                         is_direct)
                WITH RECURSIVE c AS (
                  SELECT rt.package_id, rt.version_string, rt.depth FROM resolves_to rt
                  JOIN package p ON p.package_id = rt.package_id
                  WHERE rt.service_id = :s AND rt.deploy_seq = :q AND p.ecosystem = ANY(:ecos)
                  UNION
                  SELECT d.ref_package_id, d.ref_version_string, c.depth + 1
                  FROM depends_on d
                  JOIN c ON (d.dep_package_id, d.dep_version_string)
                          = (c.package_id, c.version_string)
                  WHERE c.depth < 10)
                SELECT :s, :q, package_id, version_string, MIN(depth), FALSE FROM c
                GROUP BY package_id, version_string
                ON CONFLICT DO NOTHING
                """),
            {"s": service_id, "q": seq, "ecos": manifest_ecosystems},
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
                    "environment": body.environment,
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
    by_name = {(r.ecosystem, r.name, r.version): r for r in real}
    return {by_name[(k.ecosystem, k.name, k.version)]: v for k, v in data.items()}


def _groups(docs: list[dict[str, Any]]) -> tuple[list[list[ParsedPackage]], list[_Group]]:
    """Parse every document (ValueError when malformed): (each document's packages, one group per
    ecosystem). Within an ecosystem a lockfile wins over its sibling manifests.

    shortcut: with several lockfiles of one ecosystem only the first (by filename) is used; upgrade
    to merging them if a repository really keeps two.
    """
    pyproject = next(
        (
            d["raw_content"]
            for d in docs
            if d.get("filename", "").lower().endswith("pyproject.toml")
        ),
        None,
    )
    per_doc = [
        parse_manifest(d["ecosystem"], d["raw_content"], d.get("filename", ""), pyproject=pyproject)
        for d in docs
    ]
    by_ecosystem: dict[str, list[tuple[bool, list[ParsedPackage]]]] = {}
    for d, parsed in zip(docs, per_doc, strict=True):
        eco = d["ecosystem"]
        by_ecosystem.setdefault(eco, []).append((is_lockfile(eco, d.get("filename", "")), parsed))
    groups = []
    for eco, items in by_ecosystem.items():
        if locks := [parsed for lock, parsed in items if lock]:
            groups.append(_Group(eco, locks[0], True))
        elif merged := list(
            {p.name: p for _, parsed in reversed(items) for p in reversed(parsed)}.values()
        ):
            groups.append(_Group(eco, merged[::-1], False))
    return per_doc, [g for g in groups if g.parsed]


async def _project_many(session: AsyncSession, docs: list[dict[str, Any]]) -> dict[str, Any]:
    """One-way projection of stored raw manifests (Mongo) of ONE service into Postgres, as a single
    production (or staging/preview) deployment; the only write path.

    parse the documents -> fetch OSV + deps.dev (no transaction open) -> store raw advisories in
    Mongo -> ONE Postgres transaction (advisory lock, service check, packages, metadata,
    advisories, deployment + resolves_to, scan_run). Every document ends up `projected` (with
    deploy_seq) on commit or `failed` (with the error) on any exception.
    """
    started = time.perf_counter()
    service_id = docs[0]["service_id"]
    environment = docs[0].get("environment", "production")  # documents stored before 010 have none
    try:
        try:
            per_doc, groups = _groups(docs)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        if not groups:
            raise HTTPException(422, "No versioned packages found in manifest")
        hits, advisories, meta = await _fetch_upstream(
            [PkgRef(0, g.ecosystem, p.name, p.version) for g in groups for p in g.parsed]
        )
        try:  # raw advisories land in Mongo before Postgres derives vulnerability rows
            await osv_fetcher.store_raw(advisories, required=True)
        except PyMongoError as e:
            raise _mongo_down(e) from e
        async with session.begin():
            await lock_service(session, service_id)
            svc = await require_service(
                session, service_id, None
            )  # deleted since the first check -> 404
            inserted = 0
            for g in groups:
                g.refs, new = await _upsert_packages(session, g.ecosystem, g.parsed)
                inserted += new
            refs = [r for g in groups for r in g.refs]
            await apply_metadata(session, _rekey(refs, meta))
            vulns = await osv_fetcher.upsert_advisories(
                session, refs, _rekey(refs, hits), advisories
            )
            seq = await _record_deployment(session, service_id, environment, groups)
            await _record_scan(session, service_id)
    except Exception as e:
        for doc in docs:
            await _mark(doc["_id"], "failed", error=_error_text(e))
        raise
    for doc, parsed in zip(docs, per_doc, strict=True):
        packages = [
            {"name": p.name, "version": p.version, "is_direct": p.is_direct} for p in parsed
        ]
        await _mark(doc["_id"], "projected", deploy_seq=seq, parsed_packages=packages)
    # the upserted advisories and package metadata are catalogue data other teams' results use too
    await cache.bump([svc["team_id"]], catalog=True)
    return {
        "manifest_id": str(docs[0]["_id"]),
        "deploy_seq": seq,
        "packages_parsed": sum(len(g.parsed) for g in groups),
        "packages_inserted": inserted,
        "vulns_fetched": vulns,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }


async def ingest_many(session: AsyncSession, bodies: list[IngestRequest]) -> dict[str, Any]:
    """Store each raw manifest in Mongo, then project them all as ONE deployment of the service."""
    return await _project_many(session, [await _store_manifest(b) for b in bodies])


async def ingest(session: AsyncSession, body: IngestRequest) -> dict[str, Any]:
    return await ingest_many(session, [body])


@router.post("/manifest", response_model=Envelope[IngestResult])
async def ingest_manifest(
    body: IngestRequest, session: DbSession, member: CurrentMember
) -> dict[str, object]:
    """Store the raw manifest in Mongo (status `received`), then project it into Postgres.

    Mongo is the raw layer and the source of the projection, so if the raw document (or the
    raw advisories) cannot be written the request fails with 503 and Postgres is not touched.
    """
    async with session.begin():
        await require_service(session, body.service_id, member)
    return ok(await ingest(session, body))


@router.post("/replay/{manifest_id}", response_model=Envelope[IngestResult])
async def replay_manifest(
    manifest_id: str, session: DbSession, member: CurrentMember
) -> dict[str, object]:
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
        await require_service(session, doc["service_id"], member)
    return ok(await _project_many(session, [doc]))


@router.post("/scan/{service_id}", response_model=Envelope[ScanResult])
async def scan_service(
    service_id: BigId, session: DbSession, member: CurrentMember
) -> dict[str, object]:
    """Re-query OSV for the latest production deployment; raw advisories go to Mongo first.

    Unlike ingest, a Mongo outage does not fail the scan (store_raw is best-effort here).
    """
    started = time.perf_counter()
    async with session.begin():
        await require_service(session, service_id, member)
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
        svc = await require_service(session, service_id, None)
        if await latest_seq(session, service_id) != seq:  # a manifest landed mid-scan
            raise HTTPException(409, "Service changed during scan; retry")
        vulns = await osv_fetcher.upsert_advisories(session, refs, hits, advisories)
        await _record_scan(session, service_id)
    await cache.bump([svc["team_id"]], catalog=True)  # advisories are shared catalogue data
    return ok(
        {
            "packages_scanned": len(refs),
            "vulns_fetched": vulns,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    )
