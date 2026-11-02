from fastapi import APIRouter, Depends

from app.auth import get_current_user
from app.database import DbSession
from app.schemas.analysis import StatsOut
from app.schemas.common import Envelope, ok
from app.services.license_propagator import copyleft_exposure
from app.services.queries import open_vuln_rows, prunable_rows, rows

router = APIRouter(prefix="/stats", dependencies=[Depends(get_current_user)], tags=["stats"])


@router.get("/overview", response_model=Envelope[StatsOut])
async def overview(session: DbSession) -> dict[str, object]:
    counts = (
        await rows(
            session,
            "SELECT (SELECT COUNT(*) FROM service) AS services, "
            "(SELECT COUNT(*) FROM package) AS packages",
        )
    )[0]
    unique = {r["vuln_id"]: r["severity"] for r in await open_vuln_rows(session)}
    severity_counts = {
        s: sum(v == s for v in unique.values()) for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
    }
    return ok(
        {
            **counts,
            "open_vulns": len(unique),
            "critical_vulns": severity_counts["CRITICAL"],
            "prunable_packages": len(await prunable_rows(session)),
            "copyleft_packages": len(await copyleft_exposure(session)),
            "severity_counts": severity_counts,
        }
    )
