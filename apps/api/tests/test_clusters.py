import zlib

import numpy as np
import pytest
from sqlalchemy import text

from app.database import SessionLocal, get_mongo, get_redis
from app.services import cluster_engine


def fake_encode(texts: list[str]) -> np.ndarray:
    """Deterministic bag-of-words embedding: shared words => similar vectors."""
    out = np.zeros((len(texts), 384), dtype=np.float32)
    for i, t in enumerate(texts):
        for w in t.lower().replace(":", " ").split():
            out[i] += np.random.default_rng(zlib.crc32(w.encode())).normal(size=384)
        out[i] /= np.linalg.norm(out[i]) or 1
    return out


@pytest.fixture
def fake_model(monkeypatch):
    monkeypatch.setattr(cluster_engine, "encode", fake_encode)


async def test_recompute_replaces_clusters(client, auth, fake_model, reseed):
    r = await client.post("/clusters/recompute", headers=auth)
    assert r.status_code == 202 and r.json()["data"]["status"] == "accepted"
    assert await get_redis().get(cluster_engine.LOCK_KEY) is None  # released

    clusters = (await client.get("/clusters", headers=auth)).json()["data"]
    assert len(clusters) >= 1
    assert all(c["label"] and len(c["keywords"]) <= 3 and c["member_count"] >= 5 for c in clusters)

    async with SessionLocal() as s:
        assigned = (
            await s.execute(
                text(
                    "SELECT count(DISTINCT package_id) FROM package_version "
                    "WHERE cluster_id IS NOT NULL"
                )
            )
        ).scalar_one()
    assert assigned == sum(c["member_count"] for c in clusters)  # noise keeps NULL

    cid = clusters[0]["cluster_id"]
    members = (await client.get(f"/clusters/{cid}/members", headers=auth)).json()["data"]
    assert len(members) == clusters[0]["member_count"]
    assert all(isinstance(m["x"], float) and isinstance(m["y"], float) for m in members)
    doc = await get_mongo()["package_embeddings"].find_one({"package_id": members[0]["package_id"]})
    assert len(doc["embedding"]) == 384 and doc["cluster_id"] == cid

    red = await client.get("/clusters/redundancy", headers=auth)
    assert red.status_code == 200
    assert all(x["package_count"] > 1 for x in red.json()["data"])


async def test_recompute_lock_held(client, auth, monkeypatch):
    async def boom() -> int:
        raise AssertionError("must not run")

    monkeypatch.setattr(cluster_engine, "recompute", boom)
    await get_redis().set(cluster_engine.LOCK_KEY, "1", ex=60)
    r = await client.post("/clusters/recompute", headers=auth)
    assert r.status_code == 202 and r.json()["data"]["status"] == "already_running"


CRON = {"X-Cron-Secret": "test-cron-secret"}


async def test_internal_recompute_accepted(client, fake_model, reseed):
    r = await client.post("/internal/cluster-recompute", headers=CRON)
    assert r.status_code == 202 and r.json()["data"]["status"] == "accepted"


@pytest.mark.parametrize("headers", [{}, {"X-Cron-Secret": "wrong"}])
async def test_internal_recompute_bad_secret(client, headers):
    assert (await client.post("/internal/cluster-recompute", headers=headers)).status_code == 401


async def test_internal_recompute_lock_held(client, monkeypatch):
    async def boom() -> int:
        raise AssertionError("must not run")

    monkeypatch.setattr(cluster_engine, "recompute", boom)
    await get_redis().set(cluster_engine.LOCK_KEY, "1", ex=60)
    r = await client.post("/internal/cluster-recompute", headers=CRON)
    assert r.status_code == 202 and r.json()["data"]["status"] == "already_running"


async def test_recompute_failure_releases_lock(client, auth, monkeypatch):
    async def boom() -> int:
        raise RuntimeError("model exploded")

    monkeypatch.setattr(cluster_engine, "recompute", boom)
    assert (await client.post("/clusters/recompute", headers=auth)).status_code == 202
    assert await get_redis().get(cluster_engine.LOCK_KEY) is None


async def test_release_with_stale_token_keeps_new_lock():
    redis = get_redis()
    await redis.set(cluster_engine.LOCK_KEY, "new", ex=60)
    assert not await cluster_engine.release(redis, "stale")
    assert await redis.get(cluster_engine.LOCK_KEY) == "new"
    assert await cluster_engine.release(redis, "new")
    assert await redis.get(cluster_engine.LOCK_KEY) is None


async def test_members_404_and_422(client, auth):
    assert (await client.get("/clusters/99999/members", headers=auth)).status_code == 404
    assert (await client.get("/clusters/abc/members", headers=auth)).status_code == 422


async def test_clusters_require_auth(client):
    assert (await client.get("/clusters")).status_code == 401
    assert (await client.post("/clusters/recompute")).status_code == 401


def test_top_keywords_and_small_input():
    labels = np.array([0, 0, 1, 1, -1])
    docs = [
        "http client request",
        "http client fetch",
        "yaml parser",
        "yaml serializer",
        "x",
    ]
    kw = cluster_engine.top_keywords(docs, labels)
    assert kw[0][0] in {"http", "client"} and kw[1][0] == "yaml"
    c = cluster_engine.cluster(np.zeros((3, 384)))  # below min_cluster_size: all noise
    assert set(c.labels.tolist()) == {-1}
