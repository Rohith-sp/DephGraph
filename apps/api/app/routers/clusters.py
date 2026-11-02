import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pymongo.errors import PyMongoError

from app.auth import get_current_user
from app.database import DbSession, get_mongo, get_redis
from app.schemas.analysis import ClusterMember, ClusterOut, RedundancyOut
from app.schemas.common import Envelope, ok
from app.services import cluster_engine
from app.services.queries import rows

log = structlog.get_logger()
router = APIRouter(prefix="/clusters", dependencies=[Depends(get_current_user)], tags=["clusters"])


@router.get("", response_model=Envelope[list[ClusterOut]])
async def list_clusters(session: DbSession) -> dict[str, object]:
    return ok(
        await rows(
            session,
            "SELECT cluster_id, label, keywords, member_count FROM semantic_cluster "
            "ORDER BY member_count DESC, cluster_id",
        )
    )


@router.get("/redundancy", response_model=Envelope[list[RedundancyOut]])
async def redundancy(session: DbSession) -> dict[str, object]:
    return ok(
        await rows(
            session,
            "SELECT service_id, service_name, cluster_id, cluster_label, "
            "redundant_package_count AS package_count, packages "
            "FROM v_cluster_redundancy ORDER BY redundant_package_count DESC, service_name",
        )
    )


@router.get("/{cluster_id}/members", response_model=Envelope[list[ClusterMember]])
async def members(cluster_id: int, session: DbSession) -> dict[str, object]:
    found = await rows(
        session,
        "SELECT p.package_id, p.name, p.ecosystem FROM package p WHERE p.package_id IN "
        "(SELECT package_id FROM package_version WHERE cluster_id = :id) ORDER BY p.name",
        id=cluster_id,
    )
    if not found:
        raise HTTPException(404, "Cluster not found")
    coords: dict[int, list[float]] = {}
    try:
        async for d in get_mongo()["package_embeddings"].find(
            {"cluster_id": cluster_id}, {"package_id": 1, "umap_2d": 1}
        ):
            coords[d["package_id"]] = d["umap_2d"]
    except PyMongoError as e:
        log.warning("embeddings_unavailable", error=str(e))
    return ok(
        [
            {**m, "x": (xy := coords.get(m["package_id"], [None, None]))[0], "y": xy[1]}
            for m in found
        ]
    )


@router.post("/recompute", status_code=202)
async def recompute(tasks: BackgroundTasks) -> dict[str, object]:
    return ok({"status": await cluster_engine.start(tasks)})
