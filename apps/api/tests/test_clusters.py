import json
import warnings
import zlib

import numpy as np
import pytest
import respx
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal, get_mongo, get_redis
from app.jobs import cluster as cluster_engine


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


@pytest.fixture(autouse=True)
def keep_clients_open(monkeypatch):
    """The job closes its clients on exit; the test session shares them."""

    async def noop() -> None:
        return None

    monkeypatch.setattr(cluster_engine, "close_all", noop)


async def test_recompute_replaces_clusters(client, auth, fake_model, reseed):
    assert await cluster_engine.main() == 0
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
    members = clusters[0]["members"]
    assert len(members) == clusters[0]["member_count"]
    assert all(isinstance(m["x"], float) and isinstance(m["y"], float) for m in members)
    doc = await get_mongo()["package_embeddings"].find_one({"package_id": members[0]["package_id"]})
    assert len(doc["embedding"]) == 384 and doc["cluster_id"] == cid

    red = await client.get("/clusters/redundancy", headers=auth)
    assert red.status_code == 200
    assert all(x["package_count"] > 1 for x in red.json()["data"])


CRON = {"X-Cron-Secret": "test-cron-secret"}
DISPATCH = "https://api.github.com/repos/acme/dephgraph/actions/workflows/cluster.yml/dispatches"


@pytest.fixture
def dispatch_configured(monkeypatch):
    monkeypatch.setattr(settings, "github_dispatch_token", "ghp_test")
    monkeypatch.setattr(settings, "github_repository", "acme/dephgraph")


@respx.mock
@pytest.mark.parametrize(
    "path,headers", [("/clusters/recompute", "auth"), ("/internal/cluster-recompute", CRON)]
)
async def test_recompute_dispatches_workflow(client, auth, dispatch_configured, path, headers):
    route = respx.post(DISPATCH).respond(204)
    r = await client.post(path, headers=auth if headers == "auth" else headers)
    assert r.status_code == 202 and r.json() == {"data": {"status": "dispatched"}, "error": None}
    sent = route.calls[0].request
    assert sent.headers["authorization"] == "Bearer ghp_test"
    assert json.loads(sent.content) == {"ref": "main"}


@respx.mock
async def test_recompute_without_dispatch_config_is_503(client, auth):
    r = await client.post("/clusters/recompute", headers=auth)
    assert r.status_code == 503 and "GitHub Actions" in r.json()["error"]
    assert r.json()["data"] is None and not respx.calls


@respx.mock
async def test_recompute_github_rejection_is_502(client, auth, dispatch_configured):
    respx.post(DISPATCH).respond(404, json={"message": "Not Found"})
    assert (await client.post("/clusters/recompute", headers=auth)).status_code == 502


@respx.mock
@pytest.mark.parametrize(
    "path,headers", [("/clusters/recompute", "auth"), ("/internal/cluster-recompute", CRON)]
)
async def test_recompute_lock_held(client, auth, dispatch_configured, path, headers):
    await get_redis().set(cluster_engine.LOCK_KEY, "1", ex=60)
    r = await client.post(path, headers=auth if headers == "auth" else headers)
    assert r.status_code == 202 and r.json()["data"]["status"] == "already_running"
    assert not respx.calls  # nothing dispatched while the job runs


async def test_job_skips_when_lock_held(monkeypatch):
    async def boom(*_args) -> int:
        raise AssertionError("must not run")

    monkeypatch.setattr(cluster_engine, "recompute", boom)
    await get_redis().set(cluster_engine.LOCK_KEY, "other", ex=60)
    assert await cluster_engine.main() == 0
    assert await get_redis().get(cluster_engine.LOCK_KEY) == "other"  # not ours: untouched


async def test_job_failure_exits_1_and_releases_lock(monkeypatch):
    async def boom(*_args) -> int:
        raise RuntimeError("model exploded")

    monkeypatch.setattr(cluster_engine, "recompute", boom)
    assert await cluster_engine.main() == 1
    assert await get_redis().get(cluster_engine.LOCK_KEY) is None


@pytest.mark.parametrize("headers", [{}, {"X-Cron-Secret": "wrong"}])
async def test_internal_recompute_bad_secret(client, headers):
    assert (await client.post("/internal/cluster-recompute", headers=headers)).status_code == 401


async def test_internal_recompute_lock_held(client, monkeypatch):
    async def boom(*_args) -> int:
        raise AssertionError("must not run")

    monkeypatch.setattr(cluster_engine, "recompute", boom)
    await get_redis().set(cluster_engine.LOCK_KEY, "1", ex=60)
    r = await client.post("/internal/cluster-recompute", headers=CRON)
    assert r.status_code == 202 and r.json()["data"]["status"] == "already_running"


async def test_release_with_stale_token_keeps_new_lock():
    redis = get_redis()
    await redis.set(cluster_engine.LOCK_KEY, "new", ex=60)
    assert not await cluster_engine.release(redis, "stale")
    assert await redis.get(cluster_engine.LOCK_KEY) == "new"
    assert await cluster_engine.release(redis, "new")
    assert await redis.get(cluster_engine.LOCK_KEY) is None


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


async def test_recompute_drops_stale_embeddings(client, auth, fake_model, reseed):
    # a package no longer clustered (deleted, or no description) must not keep an old vector
    emb = get_mongo()["package_embeddings"]
    await emb.update_one(
        {"package_id": 999_999}, {"$set": {"embedding": [0.0], "cluster_id": 1}}, upsert=True
    )
    assert await cluster_engine.main() == 0
    assert await emb.count_documents({"package_id": 999_999}) == 0


async def _cluster_ids() -> list[int]:
    async with SessionLocal() as s:
        return [
            r[0]
            for r in await s.execute(text("SELECT cluster_id FROM semantic_cluster ORDER BY 1"))
        ]


async def test_renew_extends_only_own_lock():
    redis = get_redis()
    await redis.set(cluster_engine.LOCK_KEY, "mine", ex=5)
    assert await cluster_engine.renew(redis, "mine")
    assert 5 < await redis.ttl(cluster_engine.LOCK_KEY) <= cluster_engine.LOCK_TTL
    await redis.expire(cluster_engine.LOCK_KEY, 7)
    assert not await cluster_engine.renew(redis, "other")
    assert await redis.ttl(cluster_engine.LOCK_KEY) <= 7


async def test_lock_stolen_mid_run_aborts_without_writing(monkeypatch, reseed):  # B13
    """Run A outlives its lock; run B takes over. A must not swap clusters or touch Mongo."""
    redis = get_redis()
    await cluster_engine.recompute()  # baseline clusters from a healthy run
    before = await _cluster_ids()
    assert before
    emb = get_mongo()["package_embeddings"]
    await emb.update_many({}, {"$set": {"marker": "baseline"}})

    def slow_encode_then_lock_expires(texts):
        # runs in a worker thread: emulate TTL expiry + a second recompute taking the lock
        import redis as sync_redis

        r = sync_redis.Redis.from_url(settings.redis_url, decode_responses=True)
        r.set(cluster_engine.LOCK_KEY, "run-b")
        r.close()
        return fake_encode(texts)

    monkeypatch.setattr(cluster_engine, "encode", slow_encode_then_lock_expires)
    await redis.set(cluster_engine.LOCK_KEY, "run-a")
    await cluster_engine.run_guarded(redis, "run-a")

    assert await _cluster_ids() == before  # no DELETE/INSERT happened
    assert await emb.count_documents({"marker": "baseline"}) == await emb.count_documents({})
    assert await redis.get(cluster_engine.LOCK_KEY) == "run-b"  # B's lock was not released by A


def test_top_keywords_survive_empty_vocabulary():  # B14
    docs = ["ab: to be", "xy: of it", "日本語: 解析", "测试: 工具", "noise: x"]
    labels = np.array([0, 0, 1, 1, -1])
    kw = cluster_engine.top_keywords(docs, labels)
    assert kw == {0: ["ab", "xy"], 1: ["日本語", "测试"]}
    assert cluster_engine._label(kw[0]) == "ab / xy"


def test_umap_runs_without_n_jobs_warning():  # A17
    vectors = np.random.default_rng(0).normal(size=(30, 8)).astype(np.float32)
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        result = cluster_engine.cluster(vectors)
    assert result.coords.shape == (30, 2)


async def test_package_without_description_loses_its_embedding(fake_model, reseed):
    emb = get_mongo()["package_embeddings"]
    await cluster_engine.recompute()
    pid = (await emb.find_one({}))["package_id"]
    async with SessionLocal() as s, s.begin():
        await s.execute(
            text("UPDATE package SET description = '  ' WHERE package_id = :p"), {"p": pid}
        )
    await cluster_engine.recompute()
    assert await emb.count_documents({"package_id": pid}) == 0
    assert await emb.count_documents({}) > 0
