from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pymongo.errors import PyMongoError
from sqlalchemy import text

from app.auth import CurrentMember, get_current_user
from app.database import DbSession
from app.models.mongo import osv_advisories
from app.schemas.analysis import BlastRadiusOut, VulnDetail, VulnOut
from app.schemas.common import BigId, Envelope, Page, Pagination, ok
from app.services.blast_radius import get_blast_radius
from app.services.cache import cached_json
from app.services.queries import LATEST, MY_SERVICES, require_service, rows, team_params

log = structlog.get_logger()
router = APIRouter(dependencies=[Depends(get_current_user)], tags=["vulnerabilities"])

SORT_COLUMNS = {"cvss_score": "v.cvss_score", "published_at": "v.published_at"}  # whitelist
# `mine`: (vuln, package, service) hits in the caller's latest production deployments. A vulnerability
# is visible only if it has a hit, and its counts are within the caller's teams.
MINE = f"""{LATEST},
mine AS (
  SELECT DISTINCT a.vuln_id, a.package_id, l.service_id
  FROM latest l
  JOIN resolves_to r ON (r.service_id, r.deploy_seq) = (l.service_id, l.deploy_seq)
  JOIN affects a ON (a.package_id, a.version_string) = (r.package_id, r.version_string)
  WHERE l.service_id IN ({MY_SERVICES}))"""
SELECT = """
SELECT v.vuln_id, v.osv_id, v.title, v.severity, v.cvss_score, v.published_at,
  (SELECT COUNT(DISTINCT m.package_id) FROM mine m WHERE m.vuln_id = v.vuln_id)
    AS affected_packages,
  (SELECT COUNT(DISTINCT m.service_id) FROM mine m WHERE m.vuln_id = v.vuln_id)
    AS affected_services,
  EXISTS (SELECT 1 FROM affects a WHERE a.vuln_id = v.vuln_id AND a.fixed_in IS NOT NULL)
    AS has_fix
FROM vulnerability v
"""
FILTERS = """
WHERE EXISTS (SELECT 1 FROM mine m WHERE m.vuln_id = v.vuln_id)
  AND (CAST(:severity AS TEXT) IS NULL OR v.severity = CAST(:severity AS TEXT))
  AND (CAST(:q AS TEXT) IS NULL OR v.osv_id ILIKE CAST(:q AS TEXT) OR v.title ILIKE CAST(:q AS TEXT))
  AND (CAST(:ecosystem AS TEXT) IS NULL OR EXISTS (
        SELECT 1 FROM affects a JOIN package p ON p.package_id = a.package_id
        WHERE a.vuln_id = v.vuln_id AND p.ecosystem = CAST(:ecosystem AS TEXT)))
"""


@router.get("/vulnerabilities", response_model=Envelope[Page[VulnOut]])
async def list_vulnerabilities(
    request: Request,
    session: DbSession,
    member: CurrentMember,
    page: Annotated[Pagination, Depends()],
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"] | None = None,
    ecosystem: Literal["npm", "pypi", "cargo", "go"] | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    sort: Literal["cvss_score", "published_at"] = "cvss_score",
    order: Literal["asc", "desc"] = "desc",
) -> Response:
    async def build() -> str:
        found = await session.execute(
            text(f"""
            WITH {MINE}
            SELECT (SELECT coalesce(json_agg(i), '[]'::json)::text FROM (
                      {SELECT} {FILTERS}
                      ORDER BY {SORT_COLUMNS[sort]} {order.upper()} NULLS LAST, v.vuln_id
                      LIMIT :limit OFFSET :offset) i) AS items,
                   (SELECT COUNT(*) FROM vulnerability v {FILTERS}) AS total
            """),
            {
                "severity": severity,
                "ecosystem": ecosystem,
                "q": f"%{q.replace('%', r'\%').replace('_', r'\_')}%" if q else None,
                "limit": page.page_size,
                "offset": page.offset,
                **team_params(member),
            },
        )
        items, total = found.one()
        return (
            f'{{"items":{items},"total":{total},"page":{page.page},"page_size":{page.page_size}}}'
        )

    return await cached_json(request, build, member)


@router.get("/vulnerabilities/{vuln_id}", response_model=Envelope[VulnDetail])
async def get_vulnerability(
    vuln_id: BigId, session: DbSession, member: CurrentMember
) -> dict[str, Any]:
    found = await rows(
        session,
        f"WITH {MINE} {SELECT} WHERE v.vuln_id = :id AND EXISTS "
        "(SELECT 1 FROM mine m WHERE m.vuln_id = v.vuln_id)",
        id=vuln_id,
        **team_params(member),
    )
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
async def blast_radius(
    service_id: BigId, session: DbSession, member: CurrentMember
) -> dict[str, Any]:
    await require_service(session, service_id, member)
    return ok(await get_blast_radius(session, service_id, member.team_ids))
