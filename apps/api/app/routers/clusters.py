import json

import structlog
from fastapi import APIRouter, Depends, Request, Response
from pymongo.errors import PyMongoError

from app.auth import CurrentMember, get_current_user
from app.database import DbSession, get_mongo
from app.schemas.analysis import ClusterOut, RedundancyOut
from app.schemas.common import Envelope, ok
from app.services import cluster_trigger
from app.services.cache import cached_json
from app.services.queries import MY_SERVICES, json_rows, team_params

log = structlog.get_logger()
router = APIRouter(prefix="/clusters", dependencies=[Depends(get_current_user)], tags=["clusters"])


@router.get("", response_model=Envelope[list[ClusterOut]])
async def list_clusters(request: Request, session: DbSession) -> Response:
    async def build() -> str:
        clusters = json.loads(
            await json_rows(
                session,
                "SELECT c.cluster_id, c.label, c.keywords, c.member_count, ("
                "  SELECT coalesce(json_agg(m ORDER BY m.name), '[]'::json) FROM ("
                "    SELECT p.package_id, p.name, p.ecosystem FROM package p WHERE p.package_id IN"
                "      (SELECT package_id FROM package_version WHERE cluster_id = c.cluster_id)"
                "  ) m) AS members "
                "FROM semantic_cluster c ORDER BY c.member_count DESC, c.cluster_id",
            )
        )
        coords: dict[tuple[int, int], list[float]] = {}
        available = True
        try:  # one query for every cluster's embeddings
            async for d in get_mongo()["package_embeddings"].find(
                {"cluster_id": {"$in": [c["cluster_id"] for c in clusters]}},
                {"package_id": 1, "cluster_id": 1, "umap_2d": 1},
            ):
                coords[(d["cluster_id"], d["package_id"])] = d["umap_2d"]
        except PyMongoError as e:
            log.warning("embeddings_unavailable", error=str(e))
            available = False
        for c in clusters:
            c["embeddings_available"] = available
            for m in c["members"]:
                m["x"], m["y"] = coords.get((c["cluster_id"], m["package_id"]), (None, None))
        return json.dumps(clusters)

    return await cached_json(request, build, None)  # catalogue, same for everyone


@router.get("/redundancy", response_model=Envelope[list[RedundancyOut]])
async def redundancy(request: Request, session: DbSession, member: CurrentMember) -> Response:
    return await cached_json(
        request,
        lambda: json_rows(
            session,
            "SELECT service_id, service_name, cluster_id, cluster_label, "
            "redundant_package_count AS package_count, packages "
            f"FROM v_cluster_redundancy WHERE service_id IN ({MY_SERVICES}) "
            "ORDER BY redundant_package_count DESC, service_name",
            **team_params(member),
        ),
        member,
    )


@router.post("/recompute", status_code=202)
async def recompute() -> dict[str, object]:
    return ok({"status": await cluster_trigger.trigger()})
