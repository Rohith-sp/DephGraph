"""Read cache + pre-serialized JSON: every write path bumps the generation; payloads equal the
old rows() + pydantic output; Redis failure never fails a request; connection loss recovers.
"""

import asyncio
import json
import time
import uuid
from datetime import datetime

import asyncpg
import pytest
from sqlalchemy import text

from app import auth as auth_module
from app.database import SessionLocal, engine, get_redis
from app.jobs import cluster as cluster_engine
from app.models.mongo import ensure_indexes
from app.routers.vulnerabilities import MINE, SELECT
from app.schemas import analysis as schemas
from app.schemas.common import Envelope, Page
from app.services import cache, maintainers
from app.services.queries import PRUNABLE_SQL, open_vuln_rows, rows
from tests.conftest import TEST_DSN
from tests.test_clusters import fake_encode
from tests.test_ingest import body as manifest_body
from tests.upstream import mock_upstreams

ALL_TEAMS = [1, 2, 3]
ALL_TEAMS_SERVICES = [1, 2, 3, 4, 5]
CRON = {"X-Cron-Secret": "test-cron-secret"}


async def gen() -> dict[str, int]:
    """Every data generation counter in Redis."""
    redis = get_redis()
    return {k: int(await redis.get(k)) async for k in redis.scan_iter("dephgraph:datagen:*")}


# ---- old rows() + pydantic output == new pre-serialized JSON -------------------------------------
def dumped(model, data) -> object:
    return json.loads(Envelope[model].model_validate({"data": data}).model_dump_json())["data"]


def norm(x):
    """ISO timestamps differ in spelling only (Z vs +00:00): compare as instants."""
    if isinstance(x, str):
        try:
            return datetime.fromisoformat(x).timestamp()
        except ValueError:
            return x
    if isinstance(x, list):
        return [norm(i) for i in x]
    if isinstance(x, dict):
        return {k: norm(v) for k, v in x.items()}
    return x


async def old_graph(sid: int) -> dict:
    async with SessionLocal() as s:
        svc = (await rows(s, "SELECT service_id, name FROM service WHERE service_id=:i", i=sid))[0]
        from app.services.blast_radius import load_closure
        from app.services.queries import latest_seq

        nodes, edges = await load_closure(s, sid, await latest_seq(s, sid))
    return {
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


async def test_graph_prunable_services_redundancy_match_old_output(client, auth):
    for sid in range(1, 6):
        got = (await client.get(f"/services/{sid}/graph", headers=auth)).json()["data"]
        assert got == dumped(schemas.GraphOut, await old_graph(sid))
        async with SessionLocal() as s:
            old = await rows(s, PRUNABLE_SQL, sid=sid, teams=ALL_TEAMS)
        got = (await client.get(f"/services/{sid}/prunable", headers=auth)).json()["data"]
        assert got == dumped(list[schemas.PrunableOut], old)
    async with SessionLocal() as s:
        old = await rows(
            s,
            "SELECT service_id, service_name, cluster_id, cluster_label, "
            "redundant_package_count AS package_count, packages "
            "FROM v_cluster_redundancy WHERE service_id = ANY(:t) ORDER BY redundant_package_count DESC, service_name",
            t=ALL_TEAMS_SERVICES,
        )
    got = (await client.get("/clusters/redundancy", headers=auth)).json()["data"]
    assert got == dumped(list[schemas.RedundancyOut], old)
    services = (await client.get("/services", headers=auth)).json()["data"]
    assert [x["service_id"] for x in services] == [1, 2, 3, 4, 5]
    assert services == dumped(list[schemas.ServiceOut], services)


@pytest.mark.parametrize(
    "qs",
    [
        "",
        "?sort=published_at&order=asc&page_size=7&page=2",
        "?severity=CRITICAL",
        "?ecosystem=pypi&q=%25_",
        "?q=a&page=99",
    ],
)
async def test_vulnerability_list_matches_old_output(client, auth, qs):
    r = await client.get(f"/vulnerabilities{qs}", headers=auth)
    assert r.status_code == 200 and r.json()["error"] is None
    page = r.json()["data"]
    again = json.loads(
        Envelope[Page[schemas.VulnOut]].model_validate({"data": page}).model_dump_json()
    )["data"]
    assert norm(page) == norm(again)  # exactly the VulnOut fields, nothing extra or missing
    if qs == "":
        async with SessionLocal() as s:
            old = await rows(
                s,
                f"WITH {MINE} {SELECT} WHERE EXISTS (SELECT 1 FROM mine m WHERE m.vuln_id = v.vuln_id) "
                "ORDER BY v.cvss_score DESC NULLS LAST, v.vuln_id LIMIT 50",
                teams=ALL_TEAMS,
            )
        assert norm(page["items"]) == norm(dumped(list[schemas.VulnOut], old))
        assert page["total"] == len(old)


async def test_stats_matches_old_python_computation(client, auth):
    async with SessionLocal() as s:
        unique = {
            r["vuln_id"]: r["severity"] for t in (1, 2, 3) for r in await open_vuln_rows(s, t)
        }
        prunable = len(await rows(s, PRUNABLE_SQL, sid=None, teams=ALL_TEAMS))
        counts = (
            await rows(
                s,
                "SELECT (SELECT COUNT(*) FROM service) AS services, (SELECT COUNT(DISTINCT "
                "rt.package_id) FROM resolves_to rt JOIN deployment d USING (service_id, "
                "deploy_seq) WHERE d.environment = 'production' AND d.deploy_seq = (SELECT "
                "MAX(deploy_seq) FROM deployment x WHERE x.service_id = d.service_id AND "
                "x.environment = 'production')) AS packages",
            )
        )[0]
    sev = {k: sum(v == k for v in unique.values()) for k in ("CRITICAL", "HIGH", "MEDIUM", "LOW")}
    want = {
        **counts,
        "open_vulns": len(unique),
        "critical_vulns": sev["CRITICAL"],
        "prunable_packages": prunable,
        "severity_counts": sev,
    }
    assert (await client.get("/stats/overview", headers=auth)).json()["data"] == want


async def test_clusters_inline_members_one_mongo_query(client, auth, monkeypatch, reseed):
    monkeypatch.setattr(cluster_engine, "encode", fake_encode)
    await ensure_indexes()
    await cluster_engine.recompute()
    got = (await client.get("/clusters", headers=auth)).json()["data"]
    assert got and all(c["members"] for c in got)
    assert all(len(c["members"]) == c["member_count"] for c in got)
    assert all(isinstance(m["x"], float) for c in got for m in c["members"])
    assert [m["name"] for m in got[0]["members"]] == sorted(m["name"] for m in got[0]["members"])
    assert (await client.get("/clusters/1/members", headers=auth)).status_code == 404  # removed


# ---- invalidation on every write path ------------------------------------------------------------
async def w_manifest(client, auth):
    with mock_upstreams():
        return await client.post("/ingest/manifest", json=manifest_body(), headers=auth)


async def w_replay(client, auth):
    first = (await w_manifest(client, auth)).json()["data"]["manifest_id"]
    before = await gen()
    with mock_upstreams():
        r = await client.post(f"/ingest/replay/{first}", headers=auth)
    assert await gen() != before
    return r


async def w_scan(client, auth):
    with mock_upstreams():
        return await client.post("/ingest/scan/1", headers=auth)


async def w_runtime(client, auth):
    return await client.post(
        "/runtime/samples",
        json={
            "service_id": 1,
            "samples": [{"name": "nope", "version": "1", "exec_count": 1}],
        },
        headers=auth,
    )


async def w_osv_sync(client, auth):
    with mock_upstreams():
        return await client.post("/internal/osv-sync", headers=CRON)


async def w_maintainers(client, auth, monkeypatch):
    async def none(_):
        return {}

    monkeypatch.setattr(maintainers, "fetch_all", none)
    return await client.post("/internal/maintainers-sync", headers=CRON)


async def w_plan_create(client, auth):
    return await client.post("/remediation/plan", json={"team_id": 1}, headers=auth)


async def w_plan_patch(client, auth):
    await w_plan_create(client, auth)
    before = await gen()
    r = await client.patch("/remediation/plan/1/actions/1", json={"status": "DONE"}, headers=auth)
    assert r.status_code == 200 and await gen() == {
        **before,
        cache.team_key(1): before[cache.team_key(1)] + 1,
    }
    return r


@pytest.mark.parametrize(
    "write",
    [w_manifest, w_replay, w_scan, w_runtime, w_osv_sync, w_plan_create, w_plan_patch],
)
async def test_write_path_bumps_generation(client, auth, reseed, write):
    before = await gen()
    r = await write(client, auth)
    assert r.status_code in (200, 201), r.text
    assert await gen() != before


async def test_maintainers_sync_bumps_generation(client, auth, monkeypatch, reseed):
    before = await gen()
    assert (await w_maintainers(client, auth, monkeypatch)).status_code == 200
    assert await gen() == {cache.CATALOG_KEY: before.get(cache.CATALOG_KEY, 0) + 1}


async def test_cluster_recompute_bumps_even_when_it_fails(monkeypatch):
    redis = get_redis()

    async def boom(*_):
        raise RuntimeError("x")

    monkeypatch.setattr(cluster_engine, "recompute", boom)
    await redis.set(cluster_engine.LOCK_KEY, "t")
    before = await gen()
    await cluster_engine.run_guarded(redis, "t")
    assert await gen() == {cache.CATALOG_KEY: before.get(cache.CATALOG_KEY, 0) + 1}


async def test_reads_serve_fresh_data_after_a_write(client, auth, reseed):
    before = (await client.get("/services/1/prunable", headers=auth)).json()["data"]
    assert (
        before and (await client.get("/services/1/prunable", headers=auth)).json()["data"] == before
    )
    dead = before[0]
    r = await client.post(
        "/runtime/samples",
        json={
            "service_id": 1,
            "samples": [{"name": dead["name"], "version": dead["version"], "exec_count": 5}],
        },
        headers=auth,
    )
    assert r.status_code == 200
    after = (await client.get("/services/1/prunable", headers=auth)).json()["data"]
    assert dead not in after and len(after) == len(before) - 1


# ---- cache behaviour -------------------------------------------------------------------------------
async def test_hit_skips_the_database_but_not_auth(client, auth, monkeypatch):
    assert (await client.get("/services", headers=auth)).status_code == 200
    monkeypatch.setattr(cache, "bump", None)  # would blow up if anything wrote
    r = await client.get("/services", headers=auth)
    assert r.status_code == 200 and r.headers["content-type"] == "application/json"
    assert (await client.get("/services")).status_code == 401  # cached, still unauthenticated
    assert (await client.get("/services", headers={"Authorization": "Bearer x"})).status_code == 401


async def test_query_string_is_part_of_the_key(client, auth):
    a = (await client.get("/vulnerabilities?page_size=1", headers=auth)).json()["data"]
    b = (await client.get("/vulnerabilities?page_size=2", headers=auth)).json()["data"]
    assert len(a["items"]) == 1 and len(b["items"]) == 2


async def test_errors_are_not_cached(client, auth):
    assert (await client.get("/services/9999/graph", headers=auth)).status_code == 404
    assert not [k async for k in get_redis().scan_iter(f"{cache.PREFIX}*")]


async def test_redis_down_never_fails_reads_or_writes(client, auth, monkeypatch, reseed):
    class Dead:
        async def get(self, *_, **__):
            raise OSError("down")

        set = mget = get

        def pipeline(self, *_, **__):
            raise OSError("down")

    monkeypatch.setattr(cache, "get_redis", lambda: Dead())
    for path in ("/services", "/stats/overview", "/services/1/graph", "/clusters"):
        assert (await client.get(path, headers=auth)).status_code == 200
    assert (await w_runtime(client, auth)).status_code == 200


async def test_racing_reader_cannot_resurrect_stale_data(monkeypatch):
    """A reader that read the generation before a write stores under that old generation."""
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_old_compute() -> str:
        started.set()
        await release.wait()
        return "old"

    reader = asyncio.create_task(cache.read_through("k", slow_old_compute))
    await started.wait()
    await cache.bump(catalog=True)  # the write commits and invalidates mid-compute
    release.set()
    assert (await reader) == ("old", False)

    async def fresh() -> str:
        return "new"

    assert await cache.read_through("k", fresh) == ("new", False)


# ---- one JWT decode per request ----------------------------------------------------------------------
async def test_token_decoded_once_per_request(client, auth, monkeypatch):
    calls = []
    real = auth_module.decode_token

    async def counting(token):
        calls.append(token)
        return await real(token)

    monkeypatch.setattr(auth_module, "decode_token", counting)
    assert (await client.get("/services", headers=auth)).status_code == 200
    assert len(calls) == 1


# ---- dropped connections recover -----------------------------------------------------------------------
async def test_pool_recovers_after_connections_are_killed(client, auth):
    assert (await client.get("/services", headers=auth)).status_code == 200  # pool now holds some
    admin = await asyncpg.connect(TEST_DSN)
    try:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid()"
        )
    finally:
        await admin.close()
    await get_redis().flushdb()  # force a DB read
    statuses = [(await client.get("/services", headers=auth)).status_code for _ in range(3)]
    assert statuses[-1] == 200 and statuses[1:] == [
        200,
        200,
    ]  # at most the first one fails
    assert engine.pool.checkedout() == 0


# ---- per-team generations ------------------------------------------------------------------------------
@pytest.fixture
async def as_user(client):
    """GET helper for a user who is a member of exactly `teams` (token minted per call)."""
    import jwt

    from app.config import settings
    from app.database import SessionLocal

    async def get(path, *teams):
        user = uuid.uuid4()
        async with SessionLocal() as s, s.begin():
            for t in teams:
                await s.execute(
                    text("INSERT INTO team_member (team_id, user_id) VALUES (:t, :u)"),
                    {"t": t, "u": user},
                )
        token = jwt.encode(
            {"sub": str(user), "aud": "authenticated", "exp": int(time.time()) + 600},
            settings.supabase_jwt_secret,
            algorithm="HS256",
        )
        return await client.get(path, headers={"Authorization": f"Bearer {token}"})

    return get


async def cached_keys() -> set[str]:
    return {k async for k in get_redis().scan_iter(f"{cache.PREFIX}*")}


async def test_write_for_one_team_does_not_evict_another_teams_entries(client, as_user, reseed):
    assert (await as_user("/services", 1)).status_code == 200
    assert (await as_user("/services", 2)).status_code == 200
    team1, team2 = sorted(await cached_keys(), key=lambda k: "teams:2" in k)
    assert "teams:1" in team1 and "teams:2" in team2

    await cache.bump([1])  # what a runtime / plan / PR-gate write for team 1 does
    cache.generations.clear()  # as if another instance did it
    assert (await as_user("/services", 2)).status_code == 200
    assert await cached_keys() == {team1, team2}  # team 2 was a hit: nothing new stored
    assert (await as_user("/services", 1)).status_code == 200
    assert len(await cached_keys() - {team1, team2}) == 1  # team 1 recomputed under its new key


@pytest.mark.parametrize("path", ["/services", "/stats/overview", "/vulnerabilities"])
async def test_catalogue_write_evicts_every_teams_entries(client, as_user, reseed, path):
    for team in (1, 2):
        assert (await as_user(path, team)).status_code == 200
    before = await cached_keys()
    assert len(before) == 2
    await cache.bump(catalog=True)  # osv-sync, maintainers-sync, cluster job
    for team in (1, 2):
        assert (await as_user(path, team)).status_code == 200
    assert len(await cached_keys() - before) == 2  # both recomputed under new keys


async def test_team_set_is_part_of_the_key(client, as_user, reseed):
    """Same path, different team sets: never share an entry (a user in 1+2 vs one in 1)."""
    both = (await as_user("/services", 1, 2)).json()["data"]
    only1 = (await as_user("/services", 1)).json()["data"]
    assert {s["team_id"] for s in both} == {1, 2} and {s["team_id"] for s in only1} == {1}


# ---- in-process copies ---------------------------------------------------------------------------------
async def test_bursts_share_one_generation_read_and_local_writes_are_visible_at_once(monkeypatch):
    redis = get_redis()
    await cache.bump([1], catalog=True)
    await cache.current([cache.CATALOG_KEY, cache.team_key(1)])  # local after the bump itself
    reads = []
    real = redis.mget

    async def counting(*a, **k):
        reads.append(a)
        return await real(*a, **k)

    monkeypatch.setattr(redis, "mget", counting)
    for _ in range(5):
        assert await cache.current([cache.CATALOG_KEY, cache.team_key(1)]) == [1, 1]
    assert reads == []  # five lookups, zero Redis commands
    await redis.incr(cache.team_key(1))  # another instance bumps: seen once the copy expires
    assert (await cache.current([cache.team_key(1)])) == [1]
    cache.generations.clear()
    assert (await cache.current([cache.team_key(1)])) == [2]
    assert len(reads) == 1
