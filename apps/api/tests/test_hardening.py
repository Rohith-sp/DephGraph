"""Oversized ids are 422 (not a 500 from the BIGINT/INT overflow); saved plan state on GET plan."""

import pytest

from app.services import cache

BIG = 10**20
OVERFLOW = [
    ("get", "/services/{}/graph"),
    ("get", "/services/{}/prunable"),
    ("get", "/vulnerabilities/{}"),
    ("get", "/blast-radius/{}"),
    ("get", "/pr-gate/verdicts/{}"),
    ("get", "/remediation/plan/{}"),
    ("post", "/ingest/scan/{}"),
    ("patch", "/remediation/plan/{}/actions/1"),
]


@pytest.mark.parametrize("bad", [BIG, 2**63, 0, -1])
@pytest.mark.parametrize("method,path", OVERFLOW)
async def test_path_ids_are_bounded(client, auth, method, path, bad):
    r = await client.request(method, path.format(bad), headers=auth, json={"status": "DONE"})
    assert r.status_code == 422, r.text
    assert r.json()["data"] is None and r.json()["error"]


@pytest.mark.parametrize("bad", [2**31, BIG])
async def test_action_seq_is_bounded_to_int(client, auth, bad):
    r = await client.patch(
        f"/remediation/plan/1/actions/{bad}", json={"status": "DONE"}, headers=auth
    )
    assert r.status_code == 422


@pytest.mark.parametrize(
    "path,body",
    [
        ("/ingest/manifest", {"ecosystem": "pypi", "content": "flask==2.0.0", "filename": "r.txt"}),
        ("/runtime/samples", {"samples": []}),
        ("/runtime/samples", {"samples": [], "deploy_seq": BIG}),
        ("/remediation/plan", {}),
    ],
)
@pytest.mark.parametrize("bad", [BIG, 0])
async def test_body_ids_are_bounded(client, auth, path, body, bad):
    key = "deploy_seq" if "deploy_seq" in body else ("team_id" if "plan" in path else "service_id")
    r = await client.post(path, json={**body, key: bad}, headers=auth)
    assert r.status_code == 422, r.text


async def test_page_is_bounded(client, auth):
    assert (await client.get(f"/vulnerabilities?page={BIG}", headers=auth)).status_code == 422


async def test_valid_max_ids_are_404_not_500(client, auth):
    assert (await client.get(f"/services/{2**63 - 1}/graph", headers=auth)).status_code == 404


# ---- GET /remediation/plan/{team}: saved state ----------------------------------------------------
async def plan(client, auth, team=1) -> list[dict]:
    return (await client.get(f"/remediation/plan/{team}", headers=auth)).json()["data"]


async def test_plan_without_saved_plan_has_null_state(client, auth):
    steps = await plan(client, auth)
    assert steps and all(
        s["plan_id"] is None and s["action_seq"] is None and s["action_status"] is None
        for s in steps
    )


async def test_saved_plan_state_survives_reload_and_new_plan_supersedes(client, auth, reseed):
    before = await plan(client, auth)
    first = (await client.post("/remediation/plan", json={"team_id": 1}, headers=auth)).json()[
        "data"
    ]
    after = await plan(client, auth)  # POST bumped the cache: not the stale null-state list
    assert [s["action_status"] for s in after] == ["PENDING"] * len(before)
    assert {s["plan_id"] for s in after} == {first["plan_id"]}
    assert [s["action_seq"] for s in after] == [s["step"] for s in after]

    r = await client.patch(
        f"/remediation/plan/{first['plan_id']}/actions/1", json={"status": "DONE"}, headers=auth
    )
    assert r.status_code == 200
    states = {s["step"]: s["action_status"] for s in await plan(client, auth)}
    assert states[1] == "DONE" and set(states.values()) <= {"DONE", "PENDING"}
    assert list(states.values()).count("DONE") == 1

    second = (await client.post("/remediation/plan", json={"team_id": 1}, headers=auth)).json()[
        "data"
    ]
    assert second["plan_id"] > first["plan_id"]
    latest = await plan(client, auth)
    assert {s["plan_id"] for s in latest} == {second["plan_id"]}
    assert {s["action_status"] for s in latest} == {"PENDING"}  # new plan supersedes the old state
    assert [s["package"] for s in latest] == [s["package"] for s in before]


async def test_saved_state_is_per_team_and_cached(client, auth, reseed):
    await client.post("/remediation/plan", json={"team_id": 1}, headers=auth)
    other = await plan(client, auth, team=2)
    assert all(s["plan_id"] is None for s in other)
    assert (await client.get("/remediation/plan/1", headers=auth)).status_code == 200
    assert [k async for k in cache.get_redis().scan_iter(f"{cache.PREFIX}*:/remediation/plan/1?")]
