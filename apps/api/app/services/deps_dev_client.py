"""deps.dev v3: license, published_at, yanked/deprecated flag per package version."""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

import httpx
import structlog
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sql import License, PackageVersion
from app.services.http import request
from app.services.manifest_parser import PkgRef

log = structlog.get_logger()

DEPS_DEV_URL = "https://api.deps.dev/v3"
SYSTEM = {"pypi": "pypi", "npm": "npm", "cargo": "cargo", "go": "go"}
COPYLEFT = ("GPL", "AGPL", "LGPL", "EUPL", "SSPL")


@dataclass(frozen=True)
class Metadata:
    license: str | None
    published_at: datetime | None
    is_yanked: bool


def is_copyleft(expr: str) -> bool:
    """'A OR B' is the user's choice, so copyleft only if every alternative is; AND = any."""
    alts = [a.replace("(", " ").replace(")", " ").split() for a in expr.split(" OR ")]
    return all(any(t.startswith(COPYLEFT) for t in toks) for toks in alts)


async def _fetch_one(client: httpx.AsyncClient, ref: PkgRef) -> Metadata | None:
    url = (
        f"{DEPS_DEV_URL}/systems/{SYSTEM[ref.ecosystem]}/packages/"
        f"{quote(ref.name, safe='')}/versions/{quote(ref.version, safe='')}"
    )
    try:
        r = await request(client, "GET", url)
        if r.status_code != 200:
            log.warning("deps_dev_miss", name=ref.name, version=ref.version, status=r.status_code)
            return None
        doc = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("deps_dev_failed", name=ref.name, version=ref.version, error=str(e))
        return None
    published = doc.get("publishedAt")
    try:
        when = datetime.fromisoformat(published) if published else None
    except ValueError:
        when = None
    licenses = doc.get("licenses") or []
    return Metadata(licenses[0][:60] if licenses else None, when, bool(doc.get("isDeprecated")))


async def fetch_metadata(refs: list[PkgRef]) -> dict[PkgRef, Metadata]:
    """Never raises: refs with failed lookups are simply absent."""
    sem = asyncio.Semaphore(10)

    async def bounded(ref: PkgRef) -> tuple[PkgRef, Metadata | None]:
        async with sem:
            return ref, await _fetch_one(client, ref)

    async with httpx.AsyncClient(timeout=15) as client:
        results = await asyncio.gather(*(bounded(r) for r in refs))
    return {ref: m for ref, m in results if m is not None}


async def apply_metadata(session: AsyncSession, meta: dict[PkgRef, Metadata]) -> None:
    """Upsert licenses and update package_version rows (caller commits)."""
    spdx = {m.license for m in meta.values() if m.license}
    ids: dict[str, int] = {}
    if spdx:
        stmt = insert(License).values(
            [{"spdx_id": s, "name": s, "is_copyleft": is_copyleft(s)} for s in sorted(spdx)]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["spdx_id"], set_={"name": stmt.excluded.name}
        ).returning(License.spdx_id, License.license_id)
        ids = {s: i for s, i in (await session.execute(stmt)).all()}
    # sorted: consistent row-lock order across concurrent ingests avoids deadlocks
    for ref, m in sorted(meta.items(), key=lambda kv: (kv[0].package_id, kv[0].version)):
        await session.execute(
            update(PackageVersion)
            .where(
                PackageVersion.package_id == ref.package_id,
                PackageVersion.version_string == ref.version,
            )
            .values(
                license_id=ids.get(m.license) if m.license else PackageVersion.license_id,
                published_at=m.published_at or PackageVersion.published_at,
                is_yanked=m.is_yanked,
            )
        )
