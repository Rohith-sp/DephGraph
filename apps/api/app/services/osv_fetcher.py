"""OSV.dev client: querybatch -> per-id details -> vulnerability/affects upserts."""

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from cvss import CVSS3, CVSS4, CVSSError
from packaging.version import InvalidVersion, Version
from pymongo import ReplaceOne
from pymongo.errors import PyMongoError
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mongo import osv_advisories
from app.models.sql import Affects, Vulnerability
from app.services.http import request
from app.services.manifest_parser import PkgRef, normalize_pypi
from app.services.queries import chunks

log = structlog.get_logger()

OSV_URL = "https://api.osv.dev/v1"
OSV_ECOSYSTEM = {"pypi": "PyPI", "npm": "npm", "cargo": "crates.io", "go": "Go"}
BATCH = 1000
CONCURRENCY = 10
MAX_PAGES = 20  # extra pages per ref before giving up
_GHSA_SEVERITY = {"CRITICAL": "CRITICAL", "HIGH": "HIGH", "MODERATE": "MEDIUM", "LOW": "LOW"}


def _osv_version(ref: PkgRef) -> str:
    return ref.version.removeprefix("v") if ref.ecosystem == "go" else ref.version


async def fetch_advisories(
    refs: list[PkgRef],
) -> tuple[dict[PkgRef, list[str]], dict[str, dict[str, Any]]]:
    """Return ({ref: [osv_id]}, {osv_id: raw advisory}). Raises httpx.HTTPError if OSV is down."""
    hits: dict[PkgRef, list[str]] = {}
    async with httpx.AsyncClient(timeout=30) as client:
        pending: list[tuple[PkgRef, str | None]] = [(r, None) for r in refs]
        for _ in range(MAX_PAGES + 1):
            if not pending:
                break
            following: list[tuple[PkgRef, str | None]] = []
            for i in range(0, len(pending), BATCH):
                chunk = pending[i : i + BATCH]
                queries = [
                    {
                        "package": {"name": r.name, "ecosystem": OSV_ECOSYSTEM[r.ecosystem]},
                        "version": _osv_version(r),
                        **({"page_token": tok} if tok else {}),
                    }
                    for r, tok in chunk
                ]
                resp = await request(
                    client, "POST", f"{OSV_URL}/querybatch", json={"queries": queries}
                )
                resp.raise_for_status()
                for (ref, _), res in zip(chunk, resp.json()["results"], strict=True):
                    hits.setdefault(ref, []).extend(v["id"] for v in res.get("vulns", []))
                    if tok := res.get("next_page_token"):
                        following.append((ref, tok))
            pending = following
        else:
            # a truncated result would make upsert_advisories delete valid affects rows
            raise httpx.HTTPError("OSV querybatch pagination did not terminate")
        hits = {r: ids for r, ids in hits.items() if ids}

        sem = asyncio.Semaphore(CONCURRENCY)

        async def detail(osv_id: str) -> tuple[str, dict[str, Any]]:
            async with sem:
                r = await request(client, "GET", f"{OSV_URL}/vulns/{osv_id}")
                r.raise_for_status()
                return osv_id, r.json()

        unique = sorted({i for ids in hits.values() for i in ids})
        advisories = dict(await asyncio.gather(*(detail(i) for i in unique)))
    return hits, advisories


def _score(osv: dict[str, Any]) -> float | None:
    scores: list[float] = []
    for sev in osv.get("severity", []):
        vec = sev.get("score", "")
        try:
            if vec.startswith("CVSS:3"):
                scores.append(float(CVSS3(vec).scores()[0]))
            elif vec.startswith("CVSS:4"):
                scores.append(float(CVSS4(vec).base_score))
        except CVSSError:
            log.warning("cvss_unparseable", osv_id=osv.get("id"), vector=vec)
    return max(scores) if scores else None


def classify(osv: dict[str, Any]) -> tuple[str | None, float | None]:
    """(severity, cvss_score): CVSS base score from the vector, else GHSA database_specific."""
    score = _score(osv)
    if score is not None:
        if score >= 9:
            return "CRITICAL", score
        if score >= 7:
            return "HIGH", score
        return ("MEDIUM" if score >= 4 else "LOW" if score > 0 else "NONE"), score
    label = str(osv.get("database_specific", {}).get("severity", "")).upper()
    return _GHSA_SEVERITY.get(label), None


def _ts(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def fixed_in(osv: dict[str, Any], ref: PkgRef) -> str | None:
    """Smallest 'fixed' version above ref.version from ECOSYSTEM/SEMVER ranges of matching entries.

    GIT ranges carry commit hashes, and unparseable versions are skipped one by one; there is
    deliberately no fallback to an arbitrary fix.
    """
    want = normalize_pypi(ref.name) if ref.ecosystem == "pypi" else ref.name
    try:
        cur = Version(_osv_version(ref))
    except InvalidVersion:
        return None
    above: list[Version] = []
    raw: dict[Version, str] = {}
    for aff in osv.get("affected", []):
        pkg = aff.get("package", {})
        name = normalize_pypi(pkg.get("name", "")) if ref.ecosystem == "pypi" else pkg.get("name")
        if name != want or pkg.get("ecosystem") != OSV_ECOSYSTEM[ref.ecosystem]:
            continue
        for rng in aff.get("ranges", []):
            if rng.get("type") not in ("ECOSYSTEM", "SEMVER"):
                continue
            for event in rng.get("events", []):
                try:
                    ver = Version(event["fixed"]) if "fixed" in event else None
                except InvalidVersion:
                    continue
                if ver is not None and ver > cur:
                    above.append(ver)
                    raw[ver] = event["fixed"]
    return raw[min(above)] if above else None


async def upsert_advisories(
    session: AsyncSession,
    refs: list[PkgRef],
    hits: dict[PkgRef, list[str]],
    advisories: dict[str, dict[str, Any]],
) -> int:
    """Make `affects` match this OSV run for `refs` (caller commits). Returns advisories written.

    Upserts vulnerability + affects rows, and deletes affects rows of the queried refs that OSV
    no longer reports (withdrawn advisory, narrowed range), so they stop being false positives.
    """
    rows = []
    for osv_id, osv in advisories.items():
        severity, score = classify(osv)
        rows.append(
            {
                "osv_id": osv_id,
                "severity": severity,
                "cvss_score": score,
                "title": (osv.get("summary") or osv_id)[:500],
                "published_at": _ts(osv.get("published")),
                "modified_at": _ts(osv.get("modified")),
            }
        )
    vuln_ids: dict[str, int] = {}
    for chunk in chunks(rows):
        stmt = insert(Vulnerability).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["osv_id"],
            set_={c: stmt.excluded[c] for c in ("severity", "cvss_score", "title", "modified_at")},
        ).returning(Vulnerability.osv_id, Vulnerability.vuln_id)
        vuln_ids.update((await session.execute(stmt)).all())

    affects = {
        (ref.package_id, ref.version, vuln_ids[i]): {
            "package_id": ref.package_id,
            "version_string": ref.version,
            "vuln_id": vuln_ids[i],
            "fixed_in": fixed_in(advisories[i], ref),
        }
        for ref, ids in hits.items()
        for i in ids
    }
    # sorted: consistent row-lock order across concurrent ingests/syncs avoids deadlocks
    for chunk in chunks([affects[k] for k in sorted(affects)]):
        ins = insert(Affects).values(chunk)
        await session.execute(
            ins.on_conflict_do_update(
                index_elements=["package_id", "version_string", "vuln_id"],
                set_={"fixed_in": ins.excluded.fixed_in},
            )
        )
    keep = sorted(affects)
    await session.execute(
        text(
            "DELETE FROM affects a USING unnest(CAST(:rp AS BIGINT[]), CAST(:rv AS TEXT[])) "
            "AS q(p, v) WHERE (a.package_id, a.version_string) = (q.p, q.v) AND NOT EXISTS ("
            "SELECT 1 FROM unnest(CAST(:kp AS BIGINT[]), CAST(:kv AS TEXT[]), "
            "CAST(:ku AS BIGINT[])) k(p, v, u) "
            "WHERE (k.p, k.v, k.u) = (a.package_id, a.version_string, a.vuln_id))"
        ),
        {
            "rp": [r.package_id for r in refs],
            "rv": [r.version for r in refs],
            "kp": [k[0] for k in keep],
            "kv": [k[1] for k in keep],
            "ku": [k[2] for k in keep],
        },
    )
    await session.execute(
        text(
            "UPDATE package_version pv SET osv_checked_at = now() FROM unnest("
            "CAST(:rp AS BIGINT[]), CAST(:rv AS TEXT[])) AS q(p, v) "
            "WHERE (pv.package_id, pv.version_string) = (q.p, q.v)"
        ),
        {"rp": [r.package_id for r in refs], "rv": [r.version for r in refs]},
    )
    return len(advisories)


async def store_raw(advisories: dict[str, dict[str, Any]], *, required: bool = False) -> bool:
    """Write verbatim OSV JSON to Mongo `osv_advisories`, one bulk upsert per osv_id (idempotent).

    Mongo is the raw layer: callers store advisories here BEFORE projecting them into Postgres
    with `upsert_advisories`. With required=False (scan / cron paths) a Mongo outage is logged
    and False is returned, so the scan still projects; with required=True the PyMongoError is
    re-raised so the caller can refuse to project data the raw layer does not hold.
    """
    now = datetime.now(UTC)
    ops = [
        ReplaceOne(
            {"osv_id": osv_id},
            {"osv_id": osv_id, "raw_osv": raw, "fetched_at": now},
            upsert=True,
        )
        for osv_id, raw in advisories.items()
    ]
    try:
        if ops:
            await osv_advisories().bulk_write(ops, ordered=False)
    except PyMongoError as e:
        log.warning("osv_raw_store_failed", error=str(e))
        if required:
            raise
        return False
    return True
