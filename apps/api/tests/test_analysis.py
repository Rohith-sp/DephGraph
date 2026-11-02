import pytest
from sqlalchemy import text

from app.database import SessionLocal, get_redis
from app.services import blast_radius

# ---- /services ----------------------------------------------------------------------------


async def test_list_services(client, auth):
    r = await client.get("/services", headers=auth)
    assert r.status_code == 200
    items = r.json()["data"]
    assert len(items) == 5
    assert set(items[0]) == {
        "service_id", "team_id", "team_name", "name", "language", "repo_url",
        "package_count", "vuln_count", "critical_count",
    }  # fmt: skip
    assert all(i["package_count"] > 0 for i in items)


async def test_services_401(client):
    assert (await client.get("/services")).status_code == 401


async def test_graph(client, auth):
    r = await client.get("/services/1/graph", headers=auth)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["service"]["service_id"] == 1
    ids = {n["id"] for n in d["nodes"]}
    assert d["nodes"] and all(n["id"] == f"{n['package_id']}@{n['version']}" for n in d["nodes"])
    assert any(n["is_direct"] and n["depth"] == 0 for n in d["nodes"])
    assert all(e["source"] in ids and e["target"] in ids for e in d["edges"])


async def test_graph_404_422(client, auth):
    assert (await client.get("/services/999/graph", headers=auth)).status_code == 404
    assert (await client.get("/services/x/graph", headers=auth)).status_code == 422
    assert (await client.get("/services/1/graph")).status_code == 401


async def test_prunable(client, auth):
    r = await client.get("/services/1/prunable", headers=auth)
    assert r.status_code == 200
    for p in r.json()["data"]:
        assert set(p) == {"package_id", "name", "ecosystem", "version", "exec_count", "deploy_seq"}
        assert p["exec_count"] == 0 and p["deploy_seq"] == 2  # latest deployment of service 1


async def test_prunable_404_422(client, auth):
    assert (await client.get("/services/999/prunable", headers=auth)).status_code == 404
    assert (await client.get("/services/x/prunable", headers=auth)).status_code == 422


# ---- /blast-radius ------------------------------------------------------------------------


async def test_blast_radius_and_cache(client, auth):
    r = await client.get("/blast-radius/1", headers=auth)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["service_id"] == 1 and d["cached"] is False
    assert d["vulnerable_packages"], "seed guarantees one vulnerable direct dep"
    p = d["vulnerable_packages"][0]
    assert p["path"][-1].startswith(p["name"]) and p["vulns"]
    assert d["total_vulns"] == len(
        {v["vuln_id"] for x in d["vulnerable_packages"] for v in x["vulns"]}
    )
    assert await get_redis().ttl("dephgraph:blast_radius:1") > 86000

    again = (await client.get("/blast-radius/1", headers=auth)).json()["data"]
    assert again["cached"] is True
    assert {**again, "cached": False} == d


async def test_blast_radius_works_when_redis_down(client, auth, monkeypatch):
    class Dead:
        async def get(self, *_, **__):
            raise OSError("down")

        set = delete = get

    monkeypatch.setattr(blast_radius, "get_redis", lambda: Dead())
    r = await client.get("/blast-radius/1", headers=auth)
    assert r.status_code == 200 and r.json()["data"]["cached"] is False
    await blast_radius.invalidate(1)  # must swallow the error too


async def test_blast_radius_path_follows_dependencies():
    async with SessionLocal() as s:
        nodes, edges = await blast_radius.load_closure(s, 1, 2)
    paths = blast_radius.shortest_paths(nodes, edges)
    deep = [n for n in nodes if n["depth"] > 0 and (n["package_id"], n["version"]) in paths]
    assert deep and all(len(paths[(n["package_id"], n["version"])]) == n["depth"] + 1 for n in deep)


async def test_blast_radius_404_422_401(client, auth):
    assert (await client.get("/blast-radius/999", headers=auth)).status_code == 404
    assert (await client.get("/blast-radius/x", headers=auth)).status_code == 422
    assert (await client.get("/blast-radius/1")).status_code == 401


# ---- /vulnerabilities ---------------------------------------------------------------------


async def test_vuln_list_default(client, auth):
    r = await client.get("/vulnerabilities", headers=auth)
    d = r.json()["data"]
    assert r.status_code == 200 and d["total"] == 15 and d["page"] == 1 and d["page_size"] == 50
    scores = [i["cvss_score"] for i in d["items"]]
    assert scores == sorted(scores, reverse=True)
    assert set(d["items"][0]) == {
        "vuln_id", "osv_id", "title", "severity", "cvss_score", "published_at",
        "affected_packages", "affected_services", "has_fix",
    }  # fmt: skip


async def test_vuln_list_filters_and_pagination(client, auth):
    crit = (await client.get("/vulnerabilities?severity=CRITICAL", headers=auth)).json()["data"]
    assert crit["total"] == 3 and {i["severity"] for i in crit["items"]} == {"CRITICAL"}
    npm = (await client.get("/vulnerabilities?ecosystem=npm&page_size=2", headers=auth)).json()[
        "data"
    ]
    assert len(npm["items"]) == 2 and npm["total"] >= 2
    page2 = (await client.get("/vulnerabilities?page=2&page_size=10", headers=auth)).json()["data"]
    assert len(page2["items"]) == 5 and page2["total"] == 15
    q = (await client.get("/vulnerabilities?q=advisory %234", headers=auth)).json()["data"]
    assert q["total"] == 1
    asc = (await client.get("/vulnerabilities?sort=published_at&order=asc", headers=auth)).json()
    dates = [i["published_at"] for i in asc["data"]["items"]]
    assert dates == sorted(dates)


@pytest.mark.parametrize(
    "qs",
    ["page=0", "page_size=201", "sort=title", "order=sideways", "severity=BOGUS", "ecosystem=x"],
)
async def test_vuln_list_422(client, auth, qs):
    assert (await client.get(f"/vulnerabilities?{qs}", headers=auth)).status_code == 422


async def test_vuln_list_sql_injection_is_inert(client, auth):
    r = await client.get(
        "/vulnerabilities", params={"q": "'; DROP TABLE vulnerability;--"}, headers=auth
    )
    assert r.status_code == 200 and r.json()["data"]["total"] == 0
    assert (await client.get("/vulnerabilities", headers=auth)).json()["data"]["total"] == 15


async def test_vuln_list_401(client):
    assert (await client.get("/vulnerabilities")).status_code == 401


async def test_vuln_detail(client, auth):
    from app.database import get_mongo

    await get_mongo()["osv_advisories"].insert_one(
        {"osv_id": "GHSA-seed-0001", "raw_osv": {"summary": "S", "details": "D"}, "fetched_at": 0}
    )
    r = await client.get("/vulnerabilities/1", headers=auth)
    d = r.json()["data"]
    assert r.status_code == 200 and d["osv_id"] == "GHSA-seed-0001"
    assert d["summary"] == "S" and d["details"] == "D"
    assert d["affected"] and set(d["affected"][0]) == {
        "package_id", "name", "ecosystem", "version", "fixed_in",
    }  # fmt: skip
    nomongo = (await client.get("/vulnerabilities/2", headers=auth)).json()["data"]
    assert nomongo["summary"] is None


async def test_vuln_detail_404_422(client, auth):
    assert (await client.get("/vulnerabilities/9999", headers=auth)).status_code == 404
    assert (await client.get("/vulnerabilities/x", headers=auth)).status_code == 422
    assert (await client.get("/vulnerabilities/1")).status_code == 401


# ---- /remediation -------------------------------------------------------------------------


async def test_plan_get(client, auth):
    r = await client.get("/remediation/plan/1", headers=auth)
    assert r.status_code == 200
    steps = r.json()["data"]
    assert steps, "team 1 has open fixable vulns"
    assert [s["step"] for s in steps] == list(range(1, len(steps) + 1))
    pcts = [s["cumulative_coverage_pct"] for s in steps]
    assert pcts == sorted(pcts) and pcts[-1] <= 100
    news = [s["new_coverage"] for s in steps]
    assert news == sorted(news, reverse=True)  # greedy: never picks a smaller gain first
    assert set(steps[0]) == {
        "step", "package", "package_id", "ecosystem", "from_version", "to_version",
        "vulns_fixed", "services_affected", "new_coverage", "cumulative_coverage_pct",
    }  # fmt: skip
    assert steps[0]["new_coverage"] == len(steps[0]["vulns_fixed"])


async def test_plan_404_422_401(client, auth):
    assert (await client.get("/remediation/plan/999", headers=auth)).status_code == 404
    assert (await client.get("/remediation/plan/x", headers=auth)).status_code == 422
    assert (await client.get("/remediation/plan/1")).status_code == 401


async def test_plan_persist_and_patch(client, auth, reseed):
    r = await client.post(
        "/remediation/plan", json={"team_id": 1, "title": "My plan"}, headers=auth
    )
    assert r.status_code == 201, r.text
    plan = r.json()["data"]
    assert plan["title"] == "My plan" and plan["status"] == "DRAFT"
    assert plan["actions"] and plan["coverage_pct"] > 0
    async with SessionLocal() as s:
        n = (
            await s.execute(
                text("SELECT count(*) FROM resolves WHERE plan_id = :p"), {"p": plan["plan_id"]}
            )
        ).scalar_one()
    assert n >= len(plan["actions"])

    url = f"/remediation/plan/{plan['plan_id']}/actions/1"
    r = await client.patch(url, json={"status": "DONE"}, headers=auth)
    assert r.status_code == 200 and r.json()["data"]["status"] == "DONE"
    assert r.json()["data"]["action_seq"] == 1


async def test_plan_post_404_422_401(client, auth):
    assert (
        await client.post("/remediation/plan", json={"team_id": 999}, headers=auth)
    ).status_code == 404
    assert (await client.post("/remediation/plan", json={}, headers=auth)).status_code == 422
    assert (await client.post("/remediation/plan", json={"team_id": 1})).status_code == 401


async def test_patch_action_404_422_401(client, auth):
    ok = {"status": "DONE"}
    assert (
        await client.patch("/remediation/plan/999/actions/1", json=ok, headers=auth)
    ).status_code == 404
    assert (
        await client.patch("/remediation/plan/1/actions/1", json={"status": "NOPE"}, headers=auth)
    ).status_code == 422
    assert (await client.patch("/remediation/plan/1/actions/1", json=ok)).status_code == 401


# ---- /stats -------------------------------------------------------------------------------


async def test_stats_overview(client, auth):
    r = await client.get("/stats/overview", headers=auth)
    d = r.json()["data"]
    assert r.status_code == 200
    assert d["services"] == 5 and d["packages"] == 50
    assert sum(d["severity_counts"].values()) == d["open_vulns"]
    assert d["critical_vulns"] == d["severity_counts"]["CRITICAL"]
    assert d["prunable_packages"] >= 0 and d["copyleft_packages"] >= 0


async def test_stats_401(client):
    assert (await client.get("/stats/overview")).status_code == 401


# ---- /internal ----------------------------------------------------------------------------


async def test_internal_osv_sync(client, reseed):
    from tests.upstream import mock_upstreams

    await get_redis().set("dephgraph:blast_radius:1", "x")
    with mock_upstreams():
        r = await client.post("/internal/osv-sync", headers={"X-Cron-Secret": "test-cron-secret"})
    assert r.status_code == 200 and r.json()["data"]["packages_scanned"] > 0
    assert await get_redis().get("dephgraph:blast_radius:1") is None


@pytest.mark.parametrize("headers", [{}, {"X-Cron-Secret": "wrong"}, {"X-Cron-Secret": ""}])
async def test_internal_osv_sync_rejects(client, headers):
    assert (await client.post("/internal/osv-sync", headers=headers)).status_code == 401
