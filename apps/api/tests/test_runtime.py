from datetime import UTC, datetime, timedelta

import pytest
from pymongo.errors import PyMongoError
from sqlalchemy import text

from app.database import SessionLocal, get_mongo
from app.schemas.runtime import MAX_SAMPLES
from app.services import runtime


async def fetch(sql: str, **p):
    async with SessionLocal() as s:
        return [dict(r) for r in (await s.execute(text(sql), p)).mappings()]


async def scalar(sql: str, **p):
    async with SessionLocal() as s:
        return (await s.execute(text(sql), p)).scalar_one()


@pytest.fixture(autouse=True)
async def clean_runtime_samples():
    try:
        await runtime.runtime_samples().delete_many({})
    except PyMongoError:
        pass


async def view_prunable(service_id: int = 1) -> list[dict]:
    """Distinct (name, version) rows of v_prunable for one service (all its deployments)."""
    return await fetch(
        "SELECT DISTINCT package_name AS name, version_string AS version FROM v_prunable "
        "WHERE service_id = :s ORDER BY 1, 2",
        s=service_id,
    )


async def dead_in(seq: int, service_id: int = 1) -> list[dict]:
    """Resolved in that deployment with no live observation (the v_prunable predicate)."""
    return await fetch(
        "SELECT DISTINCT p.name, rt.version_string AS version FROM resolves_to rt "
        "JOIN package p USING (package_id) WHERE rt.service_id = :s AND rt.deploy_seq = :q "
        "AND NOT EXISTS (SELECT 1 FROM observes o WHERE (o.service_id, o.deploy_seq, "
        "o.package_id, o.version_string) = (rt.service_id, rt.deploy_seq, rt.package_id, "
        "rt.version_string) AND o.is_live) ORDER BY 1, 2",
        s=service_id,
        q=seq,
    )


async def target() -> dict:
    """A package never live in the latest production deployment (seq 1; seq 2 is staging)."""
    return (await dead_in(1))[0]


async def resolved(service_id: int = 1, seq: int = 1) -> list[dict]:
    return await fetch(
        "SELECT p.name, rt.version_string AS version FROM resolves_to rt "
        "JOIN package p USING (package_id) WHERE rt.service_id = :s AND rt.deploy_seq = :q "
        "ORDER BY 1, 2",
        s=service_id,
        q=seq,
    )


def payload(samples, **extra):
    return {"service_id": 1, "samples": samples, **extra}


async def test_samples_happy_path_makes_package_live(client, auth, reseed):
    dead = await target()
    before = await view_prunable()
    assert dead in before
    r = await client.post(
        "/runtime/samples",
        json=payload([{"name": dead["name"], "version": dead["version"], "exec_count": 42}]),
        headers=auth,
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert r.json()["error"] is None
    assert d["deploy_seq"] == 1  # latest production deployment of service 1
    assert d["samples_received"] == 1
    assert d["observes_written"] == 1
    assert d["live_packages"] == 1
    assert d["samples_skipped"] == 0
    assert d["raw_stored"] is True

    after = await view_prunable()
    assert dead not in after
    assert len(after) == len(before) - 1
    obs = await fetch(
        "SELECT o.exec_count, o.is_live, s.source, s.service_id FROM observes o "
        "JOIN scan_run s USING (scan_id) WHERE o.scan_id = :id",
        id=d["scan_id"],
    )
    assert obs == [{"exec_count": 42, "is_live": True, "source": "cli", "service_id": 1}]

    doc = await runtime.runtime_samples().find_one({"scan_id": d["scan_id"]})
    assert doc["status"] == "projected"
    assert doc["service_id"] == 1 and doc["deploy_seq"] == 1
    assert doc["samples"] == [{"name": dead["name"], "version": dead["version"], "exec_count": 42}]


async def test_prunable_endpoint_reflects_new_window(client, auth, reseed):
    dead = await target()
    listed = (await client.get("/services/1/prunable", headers=auth)).json()["data"]
    names = {p["name"] for p in listed}
    assert dead["name"] in names
    sample = {"name": dead["name"], "version": dead["version"], "exec_count": 1}
    assert (
        await client.post("/runtime/samples", json=payload([sample]), headers=auth)
    ).status_code == 200
    rows = (await client.get("/services/1/prunable", headers=auth)).json()["data"]
    assert (dead["name"], dead["version"]) not in {(p["name"], p["version"]) for p in rows}


async def test_zero_exec_count_stays_prunable_and_unknown_is_skipped(client, auth, reseed):
    dead = await target()
    samples = [
        {"name": dead["name"], "version": dead["version"], "exec_count": 0},
        {"name": "not-a-real-pkg", "version": "1.0.0", "exec_count": 9},
        {"name": dead["name"], "version": "999.999.999", "exec_count": 3},
    ]
    r = await client.post("/runtime/samples", json=payload(samples), headers=auth)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["samples_received"] == 3
    assert d["observes_written"] == 1
    assert d["live_packages"] == 0
    assert d["samples_skipped"] == 2
    assert {"name": "not-a-real-pkg", "version": "1.0.0"} in d["skipped"]
    assert dead in await view_prunable()
    assert await scalar("SELECT count(*) FROM observes WHERE scan_id = :i", i=d["scan_id"]) == 1


async def test_duplicate_samples_are_summed(client, auth, reseed):
    pkg = (await resolved())[0]
    one = {"name": pkg["name"], "version": pkg["version"], "exec_count": 5}
    r = await client.post("/runtime/samples", json=payload([one, one]), headers=auth)
    d = r.json()["data"]
    assert d["observes_written"] == 1
    assert await scalar("SELECT exec_count FROM observes WHERE scan_id = :i", i=d["scan_id"]) == 10


async def test_complete_batch_zeroes_unseen_packages(client, auth, reseed):
    all_pkgs = await resolved()
    seen = all_pkgs[0]
    r = await client.post(
        "/runtime/samples",
        json=payload(
            [{"name": seen["name"], "version": seen["version"], "exec_count": 7}],
            complete=True,
        ),
        headers=auth,
    )
    d = r.json()["data"]
    assert d["observes_written"] == len(all_pkgs)
    assert d["live_packages"] == 1
    live = await scalar(
        "SELECT count(*) FROM observes WHERE scan_id = :i AND is_live", i=d["scan_id"]
    )
    assert live == 1


async def test_explicit_deploy_seq_and_captured_at(client, auth, reseed):
    pkg = (await resolved())[0]
    cap = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    r = await client.post(
        "/runtime/samples",
        json=payload(
            [{"name": pkg["name"], "version": pkg["version"], "exec_count": 1}],
            deploy_seq=1,
            captured_at=cap.isoformat(),
        ),
        headers=auth,
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["deploy_seq"] == 1
    started = await scalar("SELECT started_at FROM scan_run WHERE scan_id = :i", i=d["scan_id"])
    assert started == cap


async def test_unknown_service_404(client, auth):
    sample = {"name": "x", "version": "1", "exec_count": 1}
    r = await client.post(
        "/runtime/samples", json={"service_id": 999, "samples": [sample]}, headers=auth
    )
    assert r.status_code == 404
    assert r.json() == {"data": None, "error": "Service not found"}
    assert await runtime.runtime_samples().count_documents({}) == 0  # nothing stored


async def test_unknown_deploy_seq_404(client, auth):
    sample = {"name": "x", "version": "1", "exec_count": 1}
    r = await client.post("/runtime/samples", json=payload([sample], deploy_seq=99), headers=auth)
    assert r.status_code == 404
    assert r.json()["error"] == "Deployment not found"


async def test_service_without_deployment_409(client, auth, reseed):
    async with SessionLocal() as s, s.begin():
        sid = (
            await s.execute(
                text(
                    "INSERT INTO service (team_id, name) VALUES (1, 'rt-tmp') RETURNING service_id"
                )
            )
        ).scalar_one()
    sample = {"name": "x", "version": "1", "exec_count": 1}
    r = await client.post(
        "/runtime/samples", json={"service_id": sid, "samples": [sample]}, headers=auth
    )
    assert r.status_code == 409
    assert r.json()["data"] is None


async def test_service_deleted_mid_flight_marks_raw_rejected(client, auth, monkeypatch, reseed):
    async with SessionLocal() as s, s.begin():
        sid = (
            await s.execute(
                text(
                    "INSERT INTO service (team_id, name) VALUES (1, 'rt-del') RETURNING service_id"
                )
            )
        ).scalar_one()
        await s.execute(
            text("INSERT INTO deployment (service_id, deploy_seq) VALUES (:s, 1)"),
            {"s": sid},
        )
    real = runtime.lock_service

    async def delete_then_lock(session, service_id):
        async with SessionLocal() as s, s.begin():
            await s.execute(text("DELETE FROM service WHERE service_id = :i"), {"i": sid})
        await real(session, service_id)

    monkeypatch.setattr(runtime, "lock_service", delete_then_lock)
    sample = {"name": "x", "version": "1", "exec_count": 1}
    r = await client.post(
        "/runtime/samples", json={"service_id": sid, "samples": [sample]}, headers=auth
    )
    assert r.status_code == 404
    doc = await runtime.runtime_samples().find_one({"service_id": sid})
    assert doc["status"] == "rejected"
    assert doc["scan_id"] is None


async def test_mongo_down_still_projects(client, auth, monkeypatch, reseed):
    class Broken:
        async def insert_one(self, *_):
            raise PyMongoError("down")

    monkeypatch.setattr(runtime, "runtime_samples", lambda: Broken())
    pkg = (await resolved())[0]
    r = await client.post(
        "/runtime/samples",
        json=payload([{"name": pkg["name"], "version": pkg["version"], "exec_count": 2}]),
        headers=auth,
    )
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["raw_stored"] is False
    assert d["observes_written"] == 1
    assert await get_mongo()["runtime_samples"].count_documents({}) == 0


async def test_mark_failure_is_logged_not_raised(monkeypatch):
    class Broken:
        async def update_one(self, *_):
            raise PyMongoError("down")

    monkeypatch.setattr(runtime, "runtime_samples", lambda: Broken())
    from bson import ObjectId

    await runtime._mark_raw(ObjectId(), {"status": "projected"})
    await runtime._mark_raw(None, {"status": "projected"})


async def test_unauthenticated_401(client):
    r = await client.post("/runtime/samples", json=payload([GOOD]))
    assert r.status_code == 401


GOOD = {"name": "x", "version": "1", "exec_count": 1}


@pytest.mark.parametrize(
    "patch",
    [
        {"samples": []},
        {"samples": [GOOD] * (MAX_SAMPLES + 1)},
        {"samples": [{**GOOD, "exec_count": -1}]},
        {"samples": [{**GOOD, "exec_count": 2**31}]},
        {"samples": [{**GOOD, "name": ""}]},
        {"samples": [{**GOOD, "version": "v" * 101}]},
        {"samples": [{"name": "x", "version": "1"}]},
        {"service_id": "abc"},
        {"deploy_seq": 0},
        {"captured_at": "2026-01-01T00:00:00"},  # naive datetime
        {"captured_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
    ],
)
async def test_validation_422(client, auth, patch):
    r = await client.post("/runtime/samples", json={**payload([GOOD]), **patch}, headers=auth)
    assert r.status_code == 422, r.text
    assert r.json()["data"] is None
