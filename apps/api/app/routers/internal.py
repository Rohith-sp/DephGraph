import hmac
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException

from app.config import settings
from app.database import DbSession
from app.schemas.analysis import SyncOut
from app.schemas.common import Envelope, ok
from app.services import blast_radius, osv_fetcher
from app.services.manifest_parser import PkgRef
from app.services.queries import LATEST, rows

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/osv-sync", response_model=Envelope[SyncOut])
async def osv_sync(
    session: DbSession, x_cron_secret: Annotated[str | None, Header()] = None
) -> dict[str, object]:
    """Cron: re-query OSV for every package version in a latest deployment."""
    expected = settings.internal_cron_secret
    if not expected or not hmac.compare_digest((x_cron_secret or "").encode(), expected.encode()):
        raise HTTPException(401, "Invalid cron secret")
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
        vulns = await osv_fetcher.sync(session, refs) if refs else 0
    await blast_radius.invalidate()
    return ok({"packages_scanned": len(refs), "vulns_fetched": vulns})
