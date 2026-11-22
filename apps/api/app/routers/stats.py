from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import text

from app.auth import CurrentMember, get_current_user
from app.database import DbSession
from app.schemas.analysis import StatsOut
from app.schemas.common import Envelope
from app.services.cache import cached_json
from app.services.queries import LATEST, MY_SERVICES, PRUNABLE_SQL, team_params

router = APIRouter(prefix="/stats", dependencies=[Depends(get_current_user)], tags=["stats"])

# Distinct open (non-waived) vulnerabilities in the caller's latest production deployments.
OVERVIEW_SQL = f"""
WITH {LATEST},
open_vuln AS (
  SELECT DISTINCT v.vuln_id, v.severity
  FROM latest l
  JOIN resolves_to r ON (r.service_id, r.deploy_seq) = (l.service_id, l.deploy_seq)
  JOIN affects a ON (a.package_id, a.version_string) = (r.package_id, r.version_string)
  JOIN vulnerability v ON v.vuln_id = a.vuln_id
  WHERE l.service_id IN ({MY_SERVICES}) AND NOT EXISTS (
    SELECT 1 FROM waived_for w
    WHERE w.vuln_id = v.vuln_id AND w.service_id = l.service_id
      AND (w.expires_on IS NULL OR w.expires_on >= CURRENT_DATE)))
SELECT json_build_object(
  'services', (SELECT COUNT(*) FROM service WHERE service_id IN ({MY_SERVICES})),
  'packages', (SELECT COUNT(DISTINCT r.package_id) FROM latest l
               JOIN resolves_to r ON (r.service_id, r.deploy_seq) = (l.service_id, l.deploy_seq)
               WHERE l.service_id IN ({MY_SERVICES})),
  'open_vulns', (SELECT COUNT(*) FROM open_vuln),
  'critical_vulns', (SELECT COUNT(*) FROM open_vuln WHERE severity = 'CRITICAL'),
  'prunable_packages', (SELECT COUNT(*) FROM ({PRUNABLE_SQL}) p),
  'severity_counts', json_build_object(
    'CRITICAL', (SELECT COUNT(*) FROM open_vuln WHERE severity = 'CRITICAL'),
    'HIGH', (SELECT COUNT(*) FROM open_vuln WHERE severity = 'HIGH'),
    'MEDIUM', (SELECT COUNT(*) FROM open_vuln WHERE severity = 'MEDIUM'),
    'LOW', (SELECT COUNT(*) FROM open_vuln WHERE severity = 'LOW'))
  )::text
"""


@router.get("/overview", response_model=Envelope[StatsOut])
async def overview(request: Request, session: DbSession, member: CurrentMember) -> Response:
    async def build() -> str:
        params = {"sid": None, **team_params(member)}
        return str((await session.execute(text(OVERVIEW_SQL), params)).scalar_one())

    return await cached_json(request, build, member.scope)
