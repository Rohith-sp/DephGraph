from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.errors import PyMongoError

from app.auth import get_current_user
from app.database import DbSession
from app.models.mongo import osv_advisories
from app.schemas.analysis import BlastRadiusOut, VulnDetail, VulnOut
from app.schemas.common import Envelope, Page, Pagination, ok
from app.services.blast_radius import get_blast_radius
from app.services.queries import LATEST, require_service, rows

log = structlog.get_logger()
router = APIRouter(dependencies=[Depends(get_current_user)], tags=["vulnerabilities"])

SORT_COLUMNS = {"cvss_score": "v.cvss_score", "published_at": "v.published_at"}  # whitelist
SELECT = f"""
WITH {LATEST}
SELECT v.vuln_id, v.osv_id, v.title, v.severity, v.cvss_score, v.published_at,
  (SELECT COUNT(DISTINCT a.package_id) FROM affects a WHERE a.vuln_id = v.vuln_id)
    AS affected_packages,
  (SELECT COUNT(DISTINCT l.service_id) FROM affects a
     JOIN resolves_to r ON (r.package_id, r.version_string) = (a.package_id, a.version_string)
     JOIN latest l ON (l.service_id, l.deploy_seq) = (r.service_id, r.deploy_seq)
   WHERE a.vuln_id = v.vuln_id) AS affected_services,
  EXISTS (SELECT 1 FROM affects a WHERE a.vuln_id = v.vuln_id AND a.fixed_in IS NOT NULL)
    AS has_fix
FROM vulnerability v
"""
FILTERS = """
WHERE (CAST(:severity AS TEXT) IS NULL OR v.severity = CAST(:severity AS TEXT))
  AND (CAST(:q AS TEXT) IS NULL OR v.osv_id ILIKE CAST(:q AS TEXT) OR v.title ILIKE CAST(:q AS TEXT))
  AND (CAST(:ecosystem AS TEXT) IS NULL OR EXISTS (
        SELECT 1 FROM affects a JOIN package p ON p.package_id = a.package_id
        WHERE a.vuln_id = v.vuln_id AND p.ecosystem = CAST(:ecosystem AS TEXT)))
"""


@router.get("/vulnerabilities", response_model=Envelope[Page[VulnOut]])
async def list_vulnerabilities(
    session: DbSession,
    page: Annotated[Pagination, Depends()],
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"] | None = None,
    ecosystem: Literal["npm", "pypi", "cargo", "go", "maven", "nuget"] | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    sort: Literal["cvss_score", "published_at"] = "cvss_score",
    order: Literal["asc", "desc"] = "desc",
) -> dict[str, Any]:
    params = {
        "severity": severity,
        "ecosystem": ecosystem,
        "q": f"%{q.replace('%', r'\%').replace('_', r'\_')}%" if q else None,
    }
    total = (await rows(session, f"SELECT COUNT(*) AS n FROM vulnerability v {FILTERS}", **params))[
        0
    ]["n"]
    items = await rows(
        session,
        f"{SELECT} {FILTERS} ORDER BY {SORT_COLUMNS[sort]} {order.upper()} NULLS LAST, v.vuln_id "
        "LIMIT :limit OFFSET :offset",
        **params,
        limit=page.page_size,
        offset=page.offset,
    )
    return ok({"items": items, "total": total, "page": page.page, "page_size": page.page_size})


@router.get("/vulnerabilities/{vuln_id}", response_model=Envelope[VulnDetail])
async def get_vulnerability(vuln_id: int, session: DbSession) -> dict[str, Any]:
    found = await rows(session, f"{SELECT} WHERE v.vuln_id = :id", id=vuln_id)
    if not found:
        raise HTTPException(404, "Vulnerability not found")
    vuln = found[0]
    affected = await rows(
        session,
        "SELECT p.package_id, p.name, p.ecosystem, a.version_string AS version, a.fixed_in "
        "FROM affects a JOIN package p ON p.package_id = a.package_id "
        "WHERE a.vuln_id = :id ORDER BY p.name, a.version_string",
        id=vuln_id,
    )
    raw: dict[str, Any] = {}
    try:
        doc = await osv_advisories().find_one({"osv_id": vuln["osv_id"]})
        raw = (doc or {}).get("raw_osv", {})
    except PyMongoError as e:
        log.warning("osv_raw_read_failed", error=str(e))
    return ok(
        {**vuln, "summary": raw.get("summary"), "details": raw.get("details"), "affected": affected}
    )


@router.get("/blast-radius/{service_id}", response_model=Envelope[BlastRadiusOut])
async def blast_radius(service_id: int, session: DbSession) -> dict[str, Any]:
    await require_service(session, service_id)
    return ok(await get_blast_radius(session, service_id))
