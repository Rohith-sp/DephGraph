import hmac
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException

from app.config import settings
from app.database import DbSession
from app.schemas.analysis import SyncOut
from app.schemas.common import Envelope, ok
from app.services import blast_radius, cluster_engine, osv_fetcher
from app.services.manifest_parser import PkgRef
from app.services.queries import LATEST, rows


def require_cron_secret(x_cron_secret: Annotated[str | None, Header()] = None) -> None:
    expected = settings.internal_cron_secret
    if not expected or not hmac.compare_digest((x_cron_secret or "").encode(), expected.encode()):
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
    refs = [PkgRef(r["package_id"], r["ecosystem"], r["name"], r["version"]) for r in found]
    hits, advisories = await osv_fetcher.fetch_advisories(refs) if refs else ({}, {})
    async with session.begin():
        vulns = await osv_fetcher.upsert_advisories(session, hits, advisories)
    await osv_fetcher.store_raw(advisories)
    await blast_radius.invalidate()
    return ok({"packages_scanned": len(refs), "vulns_fetched": vulns})


@router.post("/cluster-recompute", status_code=202)
async def cluster_recompute(tasks: BackgroundTasks) -> dict[str, object]:
    """Cron: same lock + background job as POST /clusters/recompute, cron-secret auth."""
    return ok({"status": await cluster_engine.start(tasks)})
