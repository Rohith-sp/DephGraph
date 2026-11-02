"""OSV.dev client: querybatch -> per-id details -> vulnerability/affects upserts."""

import asyncio
from datetime import datetime
from typing import Any

import httpx
import structlog
from cvss import CVSS3, CVSS4, CVSSError
from packaging.version import InvalidVersion, Version
from pymongo.errors import PyMongoError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mongo import osv_advisories
from app.models.sql import Affects, Vulnerability
from app.services.http import request
from app.services.manifest_parser import PkgRef, normalize_pypi

log = structlog.get_logger()

OSV_URL = "https://api.osv.dev/v1"
OSV_ECOSYSTEM = {"pypi": "PyPI", "npm": "npm", "cargo": "crates.io", "go": "Go"}
BATCH = 1000
CONCURRENCY = 10
_GHSA_SEVERITY = {"CRITICAL": "CRITICAL", "HIGH": "HIGH", "MODERATE": "MEDIUM", "LOW": "LOW"}


def _osv_version(ref: PkgRef) -> str:
    return ref.version.removeprefix("v") if ref.ecosystem == "go" else ref.version


async def fetch_advisories(
    refs: list[PkgRef],
) -> tuple[dict[PkgRef, list[str]], dict[str, dict[str, Any]]]:
    """Return ({ref: [osv_id]}, {osv_id: raw advisory}). Raises httpx.HTTPError if OSV is down."""
    hits: dict[PkgRef, list[str]] = {}
    # shortcut: ignores querybatch next_page_token (>1 page of vulns per package), upgrade if seen
    async with httpx.AsyncClient(timeout=30) as client:
        for i in range(0, len(refs), BATCH):
            chunk = refs[i : i + BATCH]
            queries = [
                {
                    "package": {"name": r.name, "ecosystem": OSV_ECOSYSTEM[r.ecosystem]},
                    "version": _osv_version(r),
                }
                for r in chunk
            ]
            resp = await request(client, "POST", f"{OSV_URL}/querybatch", json={"queries": queries})
            resp.raise_for_status()
            for ref, res in zip(chunk, resp.json()["results"], strict=True):
                if ids := [v["id"] for v in res.get("vulns", [])]:
                    hits[ref] = ids

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
    """Lowest 'fixed' event above ref.version among the advisory's matching affected entries."""
    want = normalize_pypi(ref.name) if ref.ecosystem == "pypi" else ref.name
    fixes: list[str] = []
    for aff in osv.get("affected", []):
        pkg = aff.get("package", {})
        name = normalize_pypi(pkg.get("name", "")) if ref.ecosystem == "pypi" else pkg.get("name")
        if name != want or pkg.get("ecosystem") != OSV_ECOSYSTEM[ref.ecosystem]:
            continue
        fixes += [
            e["fixed"] for r in aff.get("ranges", []) for e in r.get("events", []) if "fixed" in e
        ]
    try:
        cur = Version(_osv_version(ref))
        above = [f for f in fixes if Version(f) > cur]
        return min(above, key=Version) if above else None
    except InvalidVersion:
        return fixes[0] if fixes else None


async def upsert_advisories(
    session: AsyncSession, hits: dict[PkgRef, list[str]], advisories: dict[str, dict[str, Any]]
) -> int:
    """Upsert vulnerability + affects rows (caller commits). Returns advisories written."""
    if not advisories:
        return 0
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
    stmt = insert(Vulnerability).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["osv_id"],
        set_={c: stmt.excluded[c] for c in ("severity", "cvss_score", "title", "modified_at")},
    ).returning(Vulnerability.osv_id, Vulnerability.vuln_id)
    vuln_ids = {osv_id: vid for osv_id, vid in (await session.execute(stmt)).all()}

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
    ins = insert(Affects).values(list(affects.values()))
    await session.execute(
        ins.on_conflict_do_update(
            index_elements=["package_id", "version_string", "vuln_id"],
            set_={"fixed_in": ins.excluded.fixed_in},
        )
    )
    return len(advisories)


async def store_raw(advisories: dict[str, dict[str, Any]]) -> None:
    """Best-effort copy of verbatim OSV JSON to Mongo."""
    now = datetime.now().astimezone()
    try:
        for osv_id, raw in advisories.items():
            await osv_advisories().replace_one(
                {"osv_id": osv_id},
                {"osv_id": osv_id, "raw_osv": raw, "fetched_at": now},
                upsert=True,
            )
    except PyMongoError as e:
        log.warning("osv_raw_store_failed", error=str(e))


async def sync(session: AsyncSession, refs: list[PkgRef]) -> int:
    """Fetch + upsert advisories for refs (caller commits). Returns advisories written."""
    hits, advisories = await fetch_advisories(refs)
    count = await upsert_advisories(session, hits, advisories)
    await store_raw(advisories)
    return count
