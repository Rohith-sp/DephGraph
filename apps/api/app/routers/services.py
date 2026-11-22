import json

from fastapi import APIRouter, Depends, Request, Response

from app.auth import get_current_user
from app.database import DbSession
from app.schemas.analysis import GraphOut, PrunableOut, ServiceOut
from app.schemas.common import BigId, Envelope
from app.services.blast_radius import EDGES_SQL, NODES_SQL
from app.services.cache import cached_json
from app.services.queries import (
    LATEST,
    PRUNABLE_SQL,
    json_rows,
    latest_seq,
    require_service,
)

router = APIRouter(prefix="/services", dependencies=[Depends(get_current_user)], tags=["services"])


@router.get("", response_model=Envelope[list[ServiceOut]])
async def list_services(request: Request, session: DbSession) -> Response:
    return await cached_json(
        request,
        lambda: json_rows(
            session,
            f"""
            WITH {LATEST},
            sv AS (
              SELECT l.service_id, r.package_id, r.version_string, v.vuln_id, v.severity
              FROM latest l
              JOIN resolves_to r ON (r.service_id, r.deploy_seq) = (l.service_id, l.deploy_seq)
              LEFT JOIN affects a ON (a.package_id, a.version_string)
                                   = (r.package_id, r.version_string)
                                 AND NOT EXISTS (
                                   SELECT 1 FROM waived_for w
                                   WHERE w.vuln_id = a.vuln_id AND w.service_id = l.service_id
                                     AND (w.expires_on IS NULL OR w.expires_on >= CURRENT_DATE))
              LEFT JOIN vulnerability v ON v.vuln_id = a.vuln_id)
            SELECT s.service_id, s.team_id, t.name AS team_name, s.name, s.language, s.repo_url,
                   COUNT(DISTINCT (sv.package_id, sv.version_string))
                     FILTER (WHERE sv.package_id IS NOT NULL) AS package_count,
                   COUNT(DISTINCT sv.vuln_id) AS vuln_count,
                   COUNT(DISTINCT sv.vuln_id) FILTER (WHERE sv.severity = 'CRITICAL')
                     AS critical_count
            FROM service s
            JOIN team t ON t.team_id = s.team_id
            LEFT JOIN sv ON sv.service_id = s.service_id
            GROUP BY s.service_id, t.name
            ORDER BY s.service_id
            """,
        ),
    )


@router.get("/{service_id}/graph", response_model=Envelope[GraphOut])
async def service_graph(service_id: BigId, request: Request, session: DbSession) -> Response:
    async def build() -> str:
        svc = await require_service(session, service_id)
        seq = await latest_seq(session, service_id)
        params = {"s": service_id, "q": seq}
        nodes = edges = "[]"
        if seq is not None:
            nodes = await json_rows(
                session,
                f"SELECT package_id || '@' || version AS id, n.* FROM ({NODES_SQL}) n",
                **params,
            )
            edges = await json_rows(
                session,
                "SELECT dep_package_id || '@' || dep_version_string || '->' || ref_package_id "
                "|| '@' || ref_version_string AS id, "
                "dep_package_id || '@' || dep_version_string AS source, "
                f"ref_package_id || '@' || ref_version_string AS target FROM ({EDGES_SQL}) e",
                **params,
            )
        return f'{{"service":{json.dumps(svc)},"nodes":{nodes},"edges":{edges}}}'

    return await cached_json(request, build)


@router.get("/{service_id}/prunable", response_model=Envelope[list[PrunableOut]])
async def service_prunable(service_id: BigId, request: Request, session: DbSession) -> Response:
    async def build() -> str:
        await require_service(session, service_id)
        return await json_rows(
            session,
            "SELECT package_id, name, ecosystem, version, exec_count, deploy_seq "
            f"FROM ({PRUNABLE_SQL}) p",
            sid=service_id,
        )

    return await cached_json(request, build)
