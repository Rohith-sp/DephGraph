from fastapi import APIRouter, Depends

from app.auth import get_current_user
from app.database import DbSession
from app.schemas.analysis import GraphOut, PrunableOut, ServiceOut
from app.schemas.common import Envelope, ok
from app.services.blast_radius import load_closure
from app.services.queries import (
    LATEST,
    latest_seq,
    prunable_rows,
    require_service,
    rows,
)

router = APIRouter(prefix="/services", dependencies=[Depends(get_current_user)], tags=["services"])


@router.get("", response_model=Envelope[list[ServiceOut]])
async def list_services(session: DbSession) -> dict[str, object]:
    return ok(
        await rows(
            session,
            f"""
            WITH {LATEST},
            sv AS (
              SELECT l.service_id, r.package_id, r.version_string, v.vuln_id, v.severity
              FROM latest l
              JOIN resolves_to r ON (r.service_id, r.deploy_seq) = (l.service_id, l.deploy_seq)
              LEFT JOIN affects a ON (a.package_id, a.version_string)
                                   = (r.package_id, r.version_string)
              LEFT JOIN vulnerability v ON v.vuln_id = a.vuln_id)
            SELECT s.service_id, s.team_id, t.name AS team_name, s.name, s.language, s.repo_url,
                   COUNT(DISTINCT (sv.package_id, sv.version_string)) AS package_count,
                   COUNT(DISTINCT sv.vuln_id) AS vuln_count,
                   COUNT(DISTINCT sv.vuln_id) FILTER (WHERE sv.severity = 'CRITICAL')
                     AS critical_count
            FROM service s
            JOIN team t ON t.team_id = s.team_id
            LEFT JOIN sv ON sv.service_id = s.service_id
            GROUP BY s.service_id, t.name
            ORDER BY s.service_id
            """,
        )
    )


@router.get("/{service_id}/graph", response_model=Envelope[GraphOut])
async def service_graph(service_id: int, session: DbSession) -> dict[str, object]:
    svc = await require_service(session, service_id)
    seq = await latest_seq(session, service_id)
    nodes, edges = await load_closure(session, service_id, seq) if seq is not None else ([], [])
    return ok(
        {
            "service": svc,
            "nodes": [{**n, "id": f"{n['package_id']}@{n['version']}"} for n in nodes],
            "edges": [
                {
                    "id": f"{e['dep_package_id']}@{e['dep_version_string']}"
                    f"->{e['ref_package_id']}@{e['ref_version_string']}",
                    "source": f"{e['dep_package_id']}@{e['dep_version_string']}",
                    "target": f"{e['ref_package_id']}@{e['ref_version_string']}",
                }
                for e in edges
            ],
        }
    )


@router.get("/{service_id}/prunable", response_model=Envelope[list[PrunableOut]])
async def service_prunable(service_id: int, session: DbSession) -> dict[str, object]:
    await require_service(session, service_id)
    return ok(await prunable_rows(session, service_id))
