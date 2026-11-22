"""Package + package_version row upserts shared by ingest and the PR gate."""

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sql import Package, PackageVersion
from app.services.manifest_parser import ParsedPackage, PkgRef
from app.services.queries import chunks


async def upsert_packages(
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
