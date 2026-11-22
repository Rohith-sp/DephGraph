"""Per-team tenancy: API authorization matrix, read-cache isolation, production-only analysis,
and Postgres row-level security exercised as the `authenticated` role.

Seed: team 1 = services 1, 2 (+ plan 1); team 2 = services 3, 4; team 3 = service 5.
User A is the dev token (member of every team), B belongs to team 2 only, C to no team.
Service 1 also has a staging deployment (seq 2) that must be ignored.
"""

import json
import time
import uuid

import asyncpg
import jwt
import pytest
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal, get_mongo, get_redis
from app.services import cache
from app.services.queries import LATEST, PRUNABLE_SQL
from tests.conftest import DEV_USER, TEST_DSN
from tests.test_ingest import body as manifest_body
from tests.upstream import mock_upstreams

A, B, C = DEV_USER, uuid.UUID(int=2), uuid.UUID(int=3)
TEAM_SERVICES = {1: [1, 2], 2: [3, 4], 3: [5]}


def hdr(user: uuid.UUID) -> dict[str, str]:
    token = jwt.encode(
        {"sub": str(user), "aud": "authenticated", "exp": int(time.time()) + 600},
        settings.supabase_jwt_secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
async def tenants():
    """B joins team 2 (seed only knows the dev user); removed again so tests stay independent."""
    async with SessionLocal() as s, s.begin():
        await s.execute(text("INSERT INTO team_member (team_id, user_id) VALUES (2, :u)"), {"u": B})
    yield
    async with SessionLocal() as s, s.begin():
        await s.execute(text("DELETE FROM team_member WHERE user_id = ANY(:u)"), {"u": [B, C]})


async def fetch(sql: str, **p):
    async with SessionLocal() as s:
        return [dict(r) for r in (await s.execute(text(sql), p)).mappings()]


async def scalar(sql: str, **p):
    return (await fetch(f"SELECT ({sql}) AS v", **p))[0]["v"]


async def footprint() -> tuple:
    """Everything the write endpoints could change, in Postgres and Mongo."""
    pg = await scalar(
        "SELECT (SELECT count(*) FROM deployment) || '/' || (SELECT count(*) FROM scan_run) || '/'"
        " || (SELECT count(*) FROM observes) || '/' || (SELECT count(*) FROM remediation_plan)"
        " || '/' || (SELECT count(*) FROM resolves_to)"
    )
    mongo = [await get_mongo()[c].count_documents({}) for c in ("raw_manifests", "runtime_samples")]
    return pg, *mongo


# ---- hits per service, computed independently of the app's LATEST CTE -----------------------------
LATEST_PROD = (
    "d.environment = 'production' AND d.deploy_seq = (SELECT max(x.deploy_seq) FROM deployment x "
    "WHERE x.service_id = d.service_id AND x.environment = 'production')"
)


async def vuln_hits(service_ids: list[int]) -> set[tuple[int, int, int]]:
    """(vuln_id, package_id, service_id) for the latest production deployments of the services."""
    rows = await fetch(
        "SELECT DISTINCT a.vuln_id, a.package_id, rt.service_id FROM resolves_to rt "
        "JOIN deployment d USING (service_id, deploy_seq) "
        "JOIN affects a ON (a.package_id, a.version_string) = (rt.package_id, rt.version_string) "
        f"WHERE {LATEST_PROD} AND rt.service_id = ANY(:s)",
        s=service_ids,
    )
    return {(r["vuln_id"], r["package_id"], r["service_id"]) for r in rows}


async def get(client, url: str, user: uuid.UUID = A):
    return await client.get(url, headers=hdr(user))


# ---- service-scoped endpoints: member 200, non-member / no-team 404 ---------------------------------
SERVICE_ENDPOINTS = {
    "graph": ("GET", "/services/{sid}/graph", None),
    "prunable": ("GET", "/services/{sid}/prunable", None),
    "blast-radius": ("GET", "/blast-radius/{sid}", None),
    "verdicts": ("GET", "/pr-gate/verdicts/{sid}", None),
    "scan": ("POST", "/ingest/scan/{sid}", None),
    "manifest": ("POST", "/ingest/manifest", lambda sid: manifest_body(sid)),
    "samples": (
        "POST",
        "/runtime/samples",
        lambda sid: {
            "service_id": sid,
            "samples": [{"name": "x", "version": "1", "exec_count": 1}],
        },
    ),
}


async def call(client, name: str, sid: int, user: uuid.UUID):
    method, url, make_body = SERVICE_ENDPOINTS[name]
    with mock_upstreams():
        return await client.request(
            method,
            url.format(sid=sid),
            json=make_body(sid) if make_body else None,
            headers=hdr(user),
        )


@pytest.mark.parametrize("name", SERVICE_ENDPOINTS)
@pytest.mark.parametrize(
    ("user", "sid", "status"),
    [(B, 3, 200), (B, 1, 404), (B, 5, 404), (C, 3, 404), (A, 1, 200)],
    ids=["member", "other-team", "third-team", "no-team", "all-teams"],
)
async def test_service_endpoint_matrix(client, reseed, name, user, sid, status):
    before = await footprint()
    r = await call(client, name, sid, user)
    assert r.status_code == status, r.text
    if status == 404:
        assert await footprint() == before, "a rejected write must not touch any store"
        missing = await call(client, name, 999999, user)
        assert (r.status_code, r.json()) == (missing.status_code, missing.json())  # no leak


async def test_replay_is_scoped_to_the_manifests_service(client, reseed):
    with mock_upstreams():
        own = (await client.post("/ingest/manifest", json=manifest_body(3), headers=hdr(B))).json()
        other = (
            await client.post("/ingest/manifest", json=manifest_body(1), headers=hdr(A))
        ).json()
    for user, doc, status in [(B, own, 200), (B, other, 404), (C, own, 404)]:
        before = await footprint()
        with mock_upstreams():
            r = await client.post(f"/ingest/replay/{doc['data']['manifest_id']}", headers=hdr(user))
        assert r.status_code == status, r.text
        assert (await footprint() != before) == (status == 200)


# ---- team-scoped remediation -----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("user", "team", "status"), [(B, 2, 200), (B, 1, 404), (C, 2, 404), (B, 999, 404), (A, 1, 200)]
)
async def test_plan_get(client, user, team, status):
    assert (await get(client, f"/remediation/plan/{team}", user)).status_code == status


async def test_plan_post_and_patch_by_non_member_write_nothing(client, reseed):
    before = await footprint()
    for user, team in [(B, 1), (C, 2), (C, 1)]:
        r = await client.post("/remediation/plan", json={"team_id": team}, headers=hdr(user))
        assert (r.status_code, r.json()["error"]) == (404, "Team not found")
    for user in (B, C):  # plan 1 belongs to team 1
        r = await client.patch(
            "/remediation/plan/1/actions/1", json={"status": "DONE"}, headers=hdr(user)
        )
        assert r.status_code == 404
    assert await footprint() == before
    assert await scalar(
        "SELECT status FROM remediation_action WHERE plan_id=1 AND action_seq=1"
    ) == ("PENDING")


async def test_plan_post_and_patch_by_member(client, reseed):
    r = await client.post("/remediation/plan", json={"team_id": 2}, headers=hdr(B))
    assert r.status_code == 201, r.text
    plan = r.json()["data"]
    assert plan["actions"], "team 2 has fixable vulnerabilities"
    r = await client.patch(
        f"/remediation/plan/{plan['plan_id']}/actions/1", json={"status": "DONE"}, headers=hdr(B)
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "DONE"
    r = await client.patch("/remediation/plan/1/actions/1", json={"status": "DONE"}, headers=hdr(A))
    assert r.status_code == 200


# ---- list endpoints return only the caller's teams ------------------------------------------------
async def test_services_list_per_user(client):
    ids = {
        u: [s["service_id"] for s in (await get(client, "/services", u)).json()["data"]]
        for u in (A, B, C)
    }
    assert ids == {A: [1, 2, 3, 4, 5], B: [3, 4], C: []}
    assert {s["team_id"] for s in (await get(client, "/services", B)).json()["data"]} == {2}


async def test_stats_overview_per_user(client):
    a, b, c = [(await get(client, "/stats/overview", u)).json()["data"] for u in (A, B, C)]
    assert (a["services"], b["services"], c["services"]) == (5, 2, 0)
    hits = await vuln_hits([3, 4])
    assert 0 < b["open_vulns"] <= len({v for v, _, _ in hits})
    assert b["packages"] < a["packages"]
    assert all(v == 0 for k, v in c.items() if k != "severity_counts")
    assert set(c["severity_counts"].values()) == {0}


async def test_vulnerability_list_and_detail_per_user(client):
    async def listing(user):
        r = await get(client, "/vulnerabilities?page_size=200", user)
        assert r.status_code == 200
        return {v["vuln_id"]: v for v in r.json()["data"]["items"]}, r.json()["data"]["total"]

    (a, a_total), (b, b_total), (c, c_total) = [await listing(u) for u in (A, B, C)]
    hits_b = await vuln_hits([3, 4])
    assert set(b) == {v for v, _, _ in hits_b} and b_total == len(b)
    assert set(b) < set(a) and (c, c_total) == ({}, 0)
    for vid, v in b.items():  # counts are within the caller's teams
        assert v["affected_services"] == len({s for x, _, s in hits_b if x == vid})
        assert v["affected_packages"] == len({p for x, p, _ in hits_b if x == vid})
    assert any(a[v]["affected_services"] > b[v]["affected_services"] for v in b)
    only_team1 = next(iter(set(a) - set(b)))
    mine = next(iter(b))
    for user, vid, status in [
        (B, only_team1, 404),
        (C, only_team1, 404),
        (C, mine, 404),
        (B, mine, 200),
        (A, only_team1, 200),
    ]:
        assert (await get(client, f"/vulnerabilities/{vid}", user)).status_code == status
    missing = await get(client, "/vulnerabilities/999999", B)
    hidden = await get(client, f"/vulnerabilities/{only_team1}", B)
    assert missing.json() == hidden.json()


async def test_redundancy_per_user_and_clusters_shared(client):
    red = {
        u: {x["service_id"] for x in (await get(client, "/clusters/redundancy", u)).json()["data"]}
        for u in (A, B, C)
    }
    assert red[A] and red[B] < red[A] and red[B] <= {3, 4} and red[C] == set()
    shared = [(await get(client, "/clusters", u)).json() for u in (A, B, C)]
    assert shared[0] == shared[1] == shared[2]


async def test_me(client):
    me = {u: (await get(client, "/me", u)).json()["data"] for u in (A, B, C)}
    assert me[A]["user_id"] == str(A)
    assert [(t["team_id"], t["role"]) for t in me[A]["teams"]] == [
        (1, "admin"),
        (2, "admin"),
        (3, "admin"),
    ]
    assert me[B]["teams"] == [
        {"team_id": 2, "name": "Payments", "slug": "payments", "role": "member"}
    ]
    assert me[C]["teams"] == []
    assert (await client.get("/me")).status_code == 401


async def test_user_without_team_gets_empty_lists_not_403(client):
    for url in ("/services", "/clusters/redundancy", "/stats/overview", "/vulnerabilities", "/me"):
        r = await get(client, url, C)
        assert r.status_code == 200 and r.json()["error"] is None, url


async def test_token_without_uuid_subject_is_401(client):
    token = jwt.encode(
        {"sub": "not-a-uuid", "aud": "authenticated", "exp": int(time.time()) + 60},
        settings.supabase_jwt_secret,
        algorithm="HS256",
    )
    r = await client.get("/services", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


# ---- read cache never crosses tenants --------------------------------------------------------------
CACHED = [
    "/services",
    "/vulnerabilities",
    "/stats/overview",
    "/clusters/redundancy",
    "/services/1/graph",
    "/services/1/prunable",
    "/remediation/plan/1",
]


@pytest.mark.parametrize("first", ["A-first", "B-first"])
@pytest.mark.parametrize("url", CACHED)
async def test_cache_isolates_tenants(client, url, first):
    order = (A, B) if first == "A-first" else (B, A)
    got = {}
    for user in order:
        r = await get(client, url, user)
        again = await get(client, url, user)  # second call is served from the cache
        assert again.json() == r.json()
        got[user] = (r.status_code, r.json())
    assert got[B][0] == (404 if "/1/" in url or url.endswith("/plan/1") else 200)
    assert got[A][0] == 200
    if got[B][0] == 200:
        assert got[A][1] != got[B][1], f"{url}: B received A's payload (or vice versa)"
    keys = [k async for k in get_redis().scan_iter(f"{cache.PREFIX}*:{url.split('?')[0]}?")]
    assert len(keys) == (2 if got[B][0] == 200 else 1), "one entry per tenant, errors not cached"


# ---- production-only analysis ---------------------------------------------------------------------
async def snapshot(client) -> dict:
    urls = ["/services", "/services/1/graph", "/services/1/prunable", "/stats/overview",
            "/vulnerabilities?page_size=200", "/clusters/redundancy", "/remediation/plan/1"]  # fmt: skip
    out = {u: (await get(client, u)).json()["data"] for u in urls}
    blast = (await get(client, "/blast-radius/1")).json()["data"]
    out["/blast-radius/1"] = {**blast, "cached": None}
    return out


async def test_seeded_staging_deployment_is_ignored(client):
    assert (
        await scalar("SELECT environment FROM deployment WHERE service_id=1 AND deploy_seq=2")
        == "staging"
    )
    prunable = (await get(client, "/services/1/prunable")).json()["data"]
    assert prunable and {p["deploy_seq"] for p in prunable} == {1}
    blast = (await get(client, "/blast-radius/1")).json()["data"]
    seq1 = await fetch(
        "SELECT DISTINCT a.vuln_id FROM resolves_to rt JOIN affects a ON (a.package_id, "
        "a.version_string) = (rt.package_id, rt.version_string) WHERE rt.service_id=1 AND rt.deploy_seq=1"
    )
    assert blast["total_vulns"] == len(seq1)


async def test_staging_ingest_changes_nothing_production_ingest_does(client, reseed):
    before = await snapshot(client)
    with mock_upstreams():
        r = await client.post(
            "/ingest/manifest", json={**manifest_body(1), "environment": "staging"}, headers=hdr(A)
        )
    assert r.status_code == 200, r.text
    seq = r.json()["data"]["deploy_seq"]
    assert (
        await scalar(
            "SELECT environment FROM deployment WHERE service_id=1 AND deploy_seq=:q", q=seq
        )
        == "staging"
    )
    assert await snapshot(client) == before, "a staging ingest must not change any risk number"
    with mock_upstreams():  # default environment is production
        r = await client.post("/ingest/manifest", json=manifest_body(1), headers=hdr(A))
    assert (
        await scalar(
            "SELECT environment FROM deployment WHERE service_id=1 AND deploy_seq=:q",
            q=r.json()["data"]["deploy_seq"],
        )
        == "production"
    )
    assert await snapshot(client) != before


async def test_invalid_environment_is_422(client):
    r = await client.post(
        "/ingest/manifest", json={**manifest_body(1), "environment": "dev"}, headers=hdr(A)
    )
    assert r.status_code == 422


async def test_service_without_production_deployment_has_empty_analysis(client, reseed):
    async with SessionLocal() as s, s.begin():
        sid = (
            await s.execute(
                text(
                    "INSERT INTO service (team_id, name) VALUES (2, 'staging-only') RETURNING service_id"
                )
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO deployment (service_id, deploy_seq, environment) VALUES (:s, 1, 'staging')"
            ),
            {"s": sid},
        )
        await s.execute(text(
            "INSERT INTO resolves_to SELECT :s, 1, package_id, version_string, depth, is_direct "
            "FROM resolves_to WHERE service_id = 1 AND deploy_seq = 1"), {"s": sid})  # fmt: skip
    graph = (await get(client, f"/services/{sid}/graph", B)).json()["data"]
    assert graph["nodes"] == [] and graph["edges"] == []
    assert (await get(client, f"/services/{sid}/prunable", B)).json()["data"] == []
    blast = (await get(client, f"/blast-radius/{sid}", B)).json()["data"]
    assert blast["vulnerable_packages"] == [] and blast["total_vulns"] == 0
    svc = next(
        s for s in (await get(client, "/services", B)).json()["data"] if s["service_id"] == sid
    )
    assert (svc["package_count"], svc["vuln_count"]) == (0, 0)


async def test_views_agree_with_app_queries(client, reseed):
    with mock_upstreams():  # a newer staging deployment must not move either side
        await client.post(
            "/ingest/manifest", json={**manifest_body(1), "environment": "staging"}, headers=hdr(A)
        )
    checks = {
        "v_prunable": (
            "SELECT service_id, package_name AS name, version_string AS version FROM v_prunable",
            f"SELECT service_id, name, version FROM ({PRUNABLE_SQL}) p",
        ),
        "v_copyleft_risk": (
            "SELECT service_id, package_name, version_string FROM v_copyleft_risk",
            f"WITH {LATEST} SELECT l.service_id, p.name, rt.version_string FROM latest l "
            "JOIN resolves_to rt ON (rt.service_id, rt.deploy_seq) = (l.service_id, l.deploy_seq) "
            "JOIN package p USING (package_id) JOIN package_version pv USING (package_id, version_string) "
            "JOIN license li USING (license_id) WHERE li.is_copyleft",
        ),
        "v_cluster_redundancy": (
            "SELECT service_id, cluster_id, redundant_package_count FROM v_cluster_redundancy",
            f"WITH {LATEST} SELECT l.service_id, pv.cluster_id, count(DISTINCT pv.package_id) "
            "FROM latest l JOIN resolves_to rt ON (rt.service_id, rt.deploy_seq) = (l.service_id, l.deploy_seq) "
            "JOIN package_version pv USING (package_id, version_string) WHERE pv.cluster_id IS NOT NULL "
            "GROUP BY 1, 2 HAVING count(DISTINCT pv.package_id) > 1",
        ),
    }
    for name, (view, app) in checks.items():
        if name == "v_prunable":  # PRUNABLE_SQL has binds and an ORDER BY: compare as sets
            got = {tuple(r.values()) for r in await fetch(view)}
            want = {tuple(r.values()) for r in await fetch(app, sid=None, teams=[1, 2, 3])}
            assert got == want and got
            continue
        for x, y in ((view, app), (app, view)):
            assert await fetch(f"({x}) EXCEPT ({y})") == [], f"{name}: view and app query disagree"


# ---- Postgres row-level security as the `authenticated` role -----------------------------------------
TEAM_OWNED = {  # table -> predicate selecting the rows a member of team 2 (services 3, 4) may see
    "team": "team_id = 2",
    "service": "team_id = 2",
    "team_member": "team_id = 2 AND user_id = :b",
    "remediation_plan": "team_id = 2",
    "remediation_action": "plan_id IN (SELECT plan_id FROM remediation_plan WHERE team_id = 2)",
    "resolves": "plan_id IN (SELECT plan_id FROM remediation_plan WHERE team_id = 2)",
    **{
        t: "service_id IN (3, 4)"
        for t in (
            "deployment", "scan_run", "pr_review", "pr_risk_verdict", "waived_for", "resolves_to", "observes",
        )  # fmt: skip
    },
}
CATALOGUE = ["package", "package_version", "vulnerability", "license", "maintainer", "maintained_by",
             "affects", "depends_on", "package_alias", "semantic_cluster"]  # fmt: skip


@pytest.fixture
async def rls():
    """(owner connection, `as_user` coroutine) on the test DB; roles reset afterwards."""
    conn = await asyncpg.connect(TEST_DSN)

    async def as_user(user: uuid.UUID | None) -> None:
        await conn.execute("RESET ROLE")
        await conn.execute("SET ROLE authenticated")
        claims = json.dumps({"sub": str(user)}) if user else ""
        await conn.execute("SELECT set_config('request.jwt.claims', $1, false)", claims)

    yield conn, as_user
    await conn.execute("RESET ROLE")
    await conn.close()


async def visible(conn, table: str) -> int:
    return await conn.fetchval(f"SELECT count(*) FROM {table}")


async def test_rls_members_see_only_their_teams(rls):
    conn, as_user = rls  # B (fixture `tenants`) is a member of team 2 only
    want = {
        table: await conn.fetchval(
            f"SELECT count(*) FROM {table} WHERE {where}".replace(":b", f"'{B}'")
        )
        for table, where in TEAM_OWNED.items()
    }
    assert want["service"] == 2 and want["deployment"] >= 2 and want["observes"] > 0
    assert want["team_member"] == 1 and want["remediation_plan"] == 0
    await as_user(B)
    for table, n in want.items():
        assert await visible(conn, table) == n, f"{table}: member of team 2 saw wrong rows"
    assert (
        await conn.fetchval("SELECT count(*) FROM v_prunable WHERE service_id NOT IN (3, 4)") == 0
    )
    await as_user(A)
    assert await visible(conn, "service") == 5 and await visible(conn, "team") == 3
    await as_user(C)  # no membership
    for table in [*TEAM_OWNED, "v_prunable"]:
        assert await visible(conn, table) == 0, f"{table}: no-team user saw rows"
    await as_user(None)  # no JWT claims at all
    for table in TEAM_OWNED:
        assert await visible(conn, table) == 0, f"{table}: claimless session saw rows"


async def test_rls_catalogue_open_but_pii_closed(rls):
    conn, as_user = rls
    await as_user(C)
    for table in CATALOGUE:
        assert await visible(conn, table) > 0, f"{table}: catalogue must stay readable"
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await visible(conn, "maintainer_email")


async def test_every_table_has_select_policy_except_private(rls):
    conn, _ = rls
    no_policy = await conn.fetch(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT EXISTS ("
        "SELECT 1 FROM pg_policies p WHERE p.schemaname = 'public' AND p.tablename = c.relname)"
    )
    assert [r["relname"] for r in no_policy] == ["maintainer_email"]


# ---- scripts/add_team_member.py ---------------------------------------------------------------------
async def test_add_member_is_idempotent_and_updates_role(rls):
    from add_team_member import add_member

    conn, _ = rls
    user = uuid.UUID(int=77)
    try:
        for role in ("member", "member", "admin"):
            await add_member(conn, ["platform", "data"], user, role)
        got = await conn.fetch(
            "SELECT team_id, role FROM team_member WHERE user_id = $1 ORDER BY 1", user
        )
        assert [(r["team_id"], r["role"]) for r in got] == [(1, "admin"), (3, "admin")]
        with pytest.raises(SystemExit):
            await add_member(conn, ["nope"], user)
    finally:
        await conn.execute("DELETE FROM team_member WHERE user_id = $1", user)
