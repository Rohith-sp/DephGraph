import hmac
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.database import DbSession
from app.schemas.analysis import SyncOut
from app.schemas.common import Envelope, ok
from app.services import blast_radius, cluster_engine, maintainers, osv_fetcher
from app.services.manifest_parser import PkgRef
from app.services.queries import LATEST, rows


def require_cron_secret(x_cron_secret: Annotated[str | None, Header()] = None) -> None:
    expected = settings.internal_cron_secret
    if not expected or not hmac.compare_digest(
        (x_cron_secret or "").encode(), expected.encode()
    ):
        raise HTTPException(401, "Invalid cron secret")


router = APIRouter(
    prefix="/internal", dependencies=[Depends(require_cron_secret)], tags=["internal"]
)


@router.post("/osv-sync", response_model=Envelope[SyncOut])
async def osv_sync(session: DbSession) -> dict[str, object]:
    """Cron: re-query OSV for every package version in a latest deployment."""
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
    refs = [
        PkgRef(r["package_id"], r["ecosystem"], r["name"], r["version"]) for r in found
    ]
    hits, advisories = await osv_fetcher.fetch_advisories(refs) if refs else ({}, {})
    await osv_fetcher.store_raw(advisories)  # raw layer first (best-effort on the cron path)
    async with session.begin():
        vulns = await osv_fetcher.upsert_advisories(session, hits, advisories)
    await blast_radius.invalidate()
    return ok({"packages_scanned": len(refs), "vulns_fetched": vulns})


@router.post("/cluster-recompute", status_code=202)
async def cluster_recompute(tasks: BackgroundTasks) -> dict[str, object]:
    """Cron: same lock + background job as POST /clusters/recompute, cron-secret auth."""
    return ok({"status": await cluster_engine.start(tasks)})


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
    return ok(
        {
            "packages_scanned": len(pkgs),
            "packages_with_data": sum(1 for m in fetched.values() if m.people),
            "maintainers_upserted": upserted,
        }
    )
