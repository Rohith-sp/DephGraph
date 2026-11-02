"""MongoDB collection helpers + index setup (PRD §6)."""

import structlog
from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase
from pymongo.errors import PyMongoError

from app.database import get_mongo

log = structlog.get_logger()

DAY = 86400


def raw_manifests() -> AsyncIOMotorCollection:
    return get_mongo()["raw_manifests"]


def osv_advisories() -> AsyncIOMotorCollection:
    return get_mongo()["osv_advisories"]


async def ensure_indexes(db: AsyncIOMotorDatabase | None = None) -> bool:
    """Create all indexes; returns False (and logs) if Mongo is unreachable."""
    db = db if db is not None else get_mongo()
    specs = {
        "raw_manifests": [
            ([("service_id", 1), ("uploaded_at", -1)], {}),
            ([("sha256", 1)], {"unique": True}),
        ],
        "osv_advisories": [([("osv_id", 1)], {"unique": True}), ([("fetched_at", -1)], {})],
        "package_embeddings": [([("package_id", 1)], {"unique": True}), ([("cluster_id", 1)], {})],
        "runtime_samples": [
            ([("service_id", 1), ("scan_id", 1)], {}),
            ([("captured_at", 1)], {"expireAfterSeconds": 90 * DAY}),
        ],
        "review_responses": [
            ([("service_id", 1), ("pr_number", 1)], {}),
            ([("responded_at", 1)], {"expireAfterSeconds": 30 * DAY}),
        ],
    }
    try:
        for name, indexes in specs.items():
            for keys, opts in indexes:
                await db[name].create_index(keys, **opts)
    except PyMongoError as e:
        log.warning("mongo_indexes_skipped", error=str(e))
        return False
    return True
