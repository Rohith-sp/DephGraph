from pathlib import Path

import httpx
import pytest
from bson import ObjectId
from pymongo.errors import ServerSelectionTimeoutError
from sqlalchemy import text

from app.database import SessionLocal, get_mongo, get_redis
from app.models.mongo import ensure_indexes
from app.routers import ingest
from app.services import blast_radius, osv_fetcher
from tests.upstream import mock_upstreams

FIX = Path(__file__).parent / "fixtures"


def body(
    service_id=1, ecosystem="pypi", fixture="requirements_flask.txt", filename="requirements.txt"
):
    return {
        "service_id": service_id,
        "ecosystem": ecosystem,
        "content": (FIX / fixture).read_text(),
        "filename": filename,
    }


async def scalar(sql: str, **p):
    async with SessionLocal() as s:
        return (await s.execute(text(sql), p)).scalar_one()


async def test_ingest_manifest_happy(client, auth, reseed):
    key = await blast_radius.cache_key(2)  # another service's entry: vulns are shared
    await get_redis().set(key, "stale")
    with mock_upstreams():
        r = await client.post("/ingest/manifest", json=body(), headers=auth)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["packages_parsed"] == 8
    assert d["packages_inserted"] >= 1
    assert d["vulns_fetched"] == 1
    assert d["duration_ms"] < 60_000
    assert await get_redis().get(key) is None  # cache invalidated

    assert await scalar("SELECT max(deploy_seq) FROM deployment WHERE service_id = 1") == 3
    assert await scalar("SELECT count(*) FROM resolves_to WHERE service_id=1 AND deploy_seq=3") >= 8
    row = await scalar(
        "SELECT severity || ':' || cvss_score FROM vulnerability WHERE osv_id='GHSA-test-0001'"
    )
    assert row == "CRITICAL:9.80"
    assert (
        await scalar(
            "SELECT fixed_in FROM affects a JOIN vulnerability v USING (vuln_id) "
            "WHERE v.osv_id='GHSA-test-0001' LIMIT 1"
        )
        == "2.2.5"
    )
    assert await scalar(
        "SELECT bool_and(is_yanked) FROM package_version pv JOIN package p USING (package_id) "
        "WHERE p.name='flask' AND pv.version_string='2.0.1'"
    )
    assert await scalar("SELECT is_copyleft FROM license WHERE spdx_id='GPL-3.0-only'")
    raw = await get_mongo()["osv_advisories"].find_one({"osv_id": "GHSA-test-0001"})
    assert raw["raw_osv"]["summary"] == "Flask test vuln"
    assert d["deploy_seq"] == 3
    doc = await get_mongo()["raw_manifests"].find_one({"_id": ObjectId(d["manifest_id"])})
    assert doc["status"] == "projected"
    assert doc["deploy_seq"] == 3 and doc["deploy_seqs"] == [3]
    assert doc["service_id"] == 1 and len(doc["parsed_packages"]) == 8
    assert "error" not in doc and doc["projected_at"] >= doc["received_at"]


async def test_ingest_dedups_raw_manifest_by_sha256(client, auth, reseed):
    with mock_upstreams():
        for _ in range(2):
            assert (
                await client.post("/ingest/manifest", json=body(), headers=auth)
            ).status_code == 200
    assert await get_mongo()["raw_manifests"].count_documents({}) == 1


async def test_ingest_deps_dev_failure_does_not_fail(client, auth, reseed):
    with mock_upstreams(deps_dev_status=500):
        r = await client.post("/ingest/manifest", json=body(), headers=auth)
    assert r.status_code == 200


async def test_ingest_osv_down_is_502_and_rolls_back(client, auth, reseed):
    router = mock_upstreams()
    router.post("https://api.osv.dev/v1/querybatch").respond(503)
    with router:
        r = await client.post("/ingest/manifest", json=body(), headers=auth)
    assert r.status_code == 502
    assert r.json()["data"] is None
    assert await scalar("SELECT max(deploy_seq) FROM deployment WHERE service_id = 1") == 2


async def test_osv_retries_on_429(client, auth, reseed):
    router = mock_upstreams()
    router.post("https://api.osv.dev/v1/querybatch").mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json={"results": [{}] * 8})]
    )
    with router:
        r = await client.post("/ingest/manifest", json=body(), headers=auth)
    assert r.status_code == 200
    assert r.json()["data"]["vulns_fetched"] == 0


async def test_ingest_manifest_404(client, auth):
    r = await client.post("/ingest/manifest", json=body(service_id=999), headers=auth)
    assert r.status_code == 404
    assert r.json() == {"data": None, "error": "Service not found"}


@pytest.mark.parametrize(
    "patch",
    [
        {"ecosystem": "rubygems"},
        {"content": ""},
        {"content": "x" * 1_000_001},
        {"service_id": "abc"},
        {"filename": None},
    ],
)
async def test_ingest_manifest_422_validation(client, auth, patch):
    r = await client.post("/ingest/manifest", json={**body(), **patch}, headers=auth)
    assert r.status_code == 422
    assert r.json()["data"] is None


async def test_ingest_manifest_422_malformed(client, auth):
    bad = {**body(ecosystem="npm"), "content": "{nope"}
    r = await client.post("/ingest/manifest", json=bad, headers=auth)
    assert r.status_code == 422
    empty = {**body(), "content": "# nothing\n"}
    assert (await client.post("/ingest/manifest", json=empty, headers=auth)).status_code == 422


async def test_ingest_manifest_401(client):
    assert (await client.post("/ingest/manifest", json=body())).status_code == 401


async def test_ingest_go_manifest(client, auth, reseed):
    with mock_upstreams():
        r = await client.post(
            "/ingest/manifest",
            json=body(3, "go", "go_gin.mod", "go.mod"),
            headers=auth,
        )
    assert r.status_code == 200
    assert r.json()["data"]["packages_parsed"] == 3


async def test_scan_happy(client, auth, reseed):
    with mock_upstreams():
        r = await client.post("/ingest/scan/1", headers=auth)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["packages_scanned"] > 0


async def test_scan_404_and_401_and_422(client, auth):
    assert (await client.post("/ingest/scan/999", headers=auth)).status_code == 404
    assert (await client.post("/ingest/scan/1")).status_code == 401
    assert (await client.post("/ingest/scan/abc", headers=auth)).status_code == 422


async def test_identical_manifest_for_two_services_is_stored_twice(client, auth, reseed):
    with mock_upstreams():
        for sid in (1, 2):
            assert (
                await client.post("/ingest/manifest", json=body(service_id=sid), headers=auth)
            ).status_code == 200
    assert await get_mongo()["raw_manifests"].count_documents({}) == 2


async def test_ensure_indexes_drops_legacy_sha256_unique_index():
    coll = get_mongo()["raw_manifests"]
    await coll.create_index([("sha256", 1)], unique=True)
    assert await ensure_indexes()
    keys = [i["key"] for i in (await coll.index_information()).values()]
    assert [("sha256", 1)] not in keys
    assert [("service_id", 1), ("sha256", 1)] in keys


async def test_ingest_drops_overlong_names_and_versions(client, auth, reseed):
    content = f"flask==2.0.1\n{'a' * 256}==1.0\nrequests=={'1' * 101}\n"
    with mock_upstreams():
        r = await client.post("/ingest/manifest", json={**body(), "content": content}, headers=auth)
    assert r.status_code == 200
    assert r.json()["data"]["packages_parsed"] == 1


async def test_scan_osv_failure_writes_nothing(client, auth, reseed):
    count = "SELECT count(*) FROM scan_run WHERE service_id = 1"
    before = await scalar(count)
    router = mock_upstreams()
    router.post("https://api.osv.dev/v1/querybatch").respond(503)
    with router:
        r = await client.post("/ingest/scan/1", headers=auth)
    assert r.status_code == 502
    assert await scalar(count) == before


async def _new_service() -> int:
    async with SessionLocal() as s, s.begin():
        return (
            await s.execute(
                text(
                    "INSERT INTO service (team_id, name) VALUES (1, 'tmp-svc') RETURNING service_id"
                )
            )
        ).scalar_one()


async def test_ingest_inserts_packages_in_sorted_order(client, auth, reseed):
    content = "zzzpkg==1.0\naaapkg==1.0\nmmmpkg==1.0\n"
    with mock_upstreams():
        r = await client.post("/ingest/manifest", json={**body(), "content": content}, headers=auth)
    assert r.status_code == 200
    names = await scalar(
        "SELECT string_agg(name, ',' ORDER BY package_id) FROM package "
        "WHERE name IN ('zzzpkg','aaapkg','mmmpkg')"
    )
    assert names == "aaapkg,mmmpkg,zzzpkg"


async def test_ingest_service_deleted_mid_flight_is_404(client, auth, monkeypatch, reseed):
    sid = await _new_service()
    real = ingest._fetch_upstream

    async def delete_then_fetch(refs):
        async with SessionLocal() as s, s.begin():
            await s.execute(text("DELETE FROM service WHERE service_id = :i"), {"i": sid})
        return await real(refs)

    monkeypatch.setattr(ingest, "_fetch_upstream", delete_then_fetch)
    with mock_upstreams():
        r = await client.post("/ingest/manifest", json=body(service_id=sid), headers=auth)
    assert r.status_code == 404


async def test_scan_service_deleted_mid_flight_is_404(client, auth, monkeypatch, reseed):
    sid = await _new_service()
    real = ingest.lock_service

    async def delete_then_lock(session, service_id):
        async with SessionLocal() as s, s.begin():
            await s.execute(text("DELETE FROM service WHERE service_id = :i"), {"i": sid})
        await real(session, service_id)

    monkeypatch.setattr(ingest, "lock_service", delete_then_lock)
    assert (await client.post(f"/ingest/scan/{sid}", headers=auth)).status_code == 404


async def test_scan_409_when_deployment_lands_mid_scan(client, auth, monkeypatch, reseed):
    before = await scalar("SELECT count(*) FROM scan_run WHERE service_id = 1")

    async def new_deploy_then_fetch(refs):
        async with SessionLocal() as s, s.begin():
            await s.execute(
                text(
                    "INSERT INTO deployment (service_id, deploy_seq) "
                    "SELECT 1, max(deploy_seq) + 1 FROM deployment WHERE service_id = 1"
                )
            )
        return {}, {}

    monkeypatch.setattr(ingest.osv_fetcher, "fetch_advisories", new_deploy_then_fetch)
    r = await client.post("/ingest/scan/1", headers=auth)
    assert r.status_code == 409
    assert await scalar("SELECT count(*) FROM scan_run WHERE service_id = 1") == before


class _DownCollection:
    """Stands in for a Motor collection while Mongo is unreachable."""

    def __getattr__(self, _name):
        async def fail(*_a, **_k):
            raise ServerSelectionTimeoutError("mongo down")

        return fail


async def test_raw_layer_written_before_postgres_projection(client, auth, monkeypatch, reseed):
    seen = {}
    real = ingest._upsert_packages

    async def check_mongo_then_upsert(session, ecosystem, parsed):
        seen["manifest"] = await get_mongo()["raw_manifests"].find_one({"service_id": 1})
        seen["advisory"] = await get_mongo()["osv_advisories"].find_one(
            {"osv_id": "GHSA-test-0001"}
        )
        return await real(session, ecosystem, parsed)

    monkeypatch.setattr(ingest, "_upsert_packages", check_mongo_then_upsert)
    with mock_upstreams():
        r = await client.post("/ingest/manifest", json=body(), headers=auth)
    assert r.status_code == 200, r.text
    assert seen["manifest"]["status"] == "received"
    assert seen["manifest"]["raw_content"] == body()["content"]
    assert seen["advisory"]["raw_osv"]["id"] == "GHSA-test-0001"


async def test_projection_parses_the_stored_document(client, auth, monkeypatch, reseed):
    real = ingest._store_manifest

    async def store_then_swap(req):
        doc = await real(req)
        return {**doc, "raw_content": "aaapkg==1.0\n"}  # what Mongo holds wins

    monkeypatch.setattr(ingest, "_store_manifest", store_then_swap)
    with mock_upstreams():
        r = await client.post("/ingest/manifest", json=body(), headers=auth)
    assert r.status_code == 200
    assert r.json()["data"]["packages_parsed"] == 1


async def test_osv_failure_marks_manifest_failed(client, auth, reseed):
    router = mock_upstreams()
    router.post("https://api.osv.dev/v1/querybatch").respond(503)
    with router:
        r = await client.post("/ingest/manifest", json=body(), headers=auth)
    assert r.status_code == 502
    doc = await get_mongo()["raw_manifests"].find_one({"service_id": 1})
    assert doc["status"] == "failed"
    assert "HTTPStatusError" in doc["error"]
    assert "deploy_seq" not in doc
    assert await scalar("SELECT max(deploy_seq) FROM deployment WHERE service_id = 1") == 2


async def test_malformed_manifest_is_kept_raw_and_marked_failed(client, auth):
    bad = {**body(ecosystem="npm"), "content": "{nope"}
    assert (await client.post("/ingest/manifest", json=bad, headers=auth)).status_code == 422
    doc = await get_mongo()["raw_manifests"].find_one({"service_id": 1})
    assert doc["status"] == "failed" and doc["error"].startswith("422: ")


async def test_reingest_after_failure_resets_status(client, auth, reseed):
    router = mock_upstreams()
    router.post("https://api.osv.dev/v1/querybatch").respond(503)
    with router:
        assert (await client.post("/ingest/manifest", json=body(), headers=auth)).status_code == 502
    with mock_upstreams():
        r = await client.post("/ingest/manifest", json=body(), headers=auth)
    assert r.status_code == 200
    docs = await get_mongo()["raw_manifests"].find({}).to_list(None)
    assert len(docs) == 1
    assert docs[0]["status"] == "projected" and "error" not in docs[0]


async def test_replay_projects_stored_manifest_as_new_deployment(client, auth, reseed):
    with mock_upstreams():
        first = (await client.post("/ingest/manifest", json=body(), headers=auth)).json()["data"]
        r = await client.post(f"/ingest/replay/{first['manifest_id']}", headers=auth)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["manifest_id"] == first["manifest_id"]
    assert (first["deploy_seq"], d["deploy_seq"]) == (3, 4)
    assert d["packages_parsed"] == 8 and d["vulns_fetched"] == 1
    assert await scalar(
        "SELECT count(*) FROM resolves_to WHERE service_id=1 AND deploy_seq=4"
    ) == await scalar("SELECT count(*) FROM resolves_to WHERE service_id=1 AND deploy_seq=3")
    doc = await get_mongo()["raw_manifests"].find_one({"_id": ObjectId(d["manifest_id"])})
    assert doc["status"] == "projected" and doc["deploy_seqs"] == [3, 4]


async def test_replay_404_422_401(client, auth):
    r = await client.post(f"/ingest/replay/{ObjectId()}", headers=auth)
    assert r.status_code == 404
    assert r.json() == {"data": None, "error": "Manifest not found"}
    assert (await client.post("/ingest/replay/not-an-id", headers=auth)).status_code == 422
    assert (await client.post(f"/ingest/replay/{ObjectId()}")).status_code == 401


async def test_replay_service_gone_is_404(client, auth, reseed):
    sid = await _new_service()
    with mock_upstreams():
        r = await client.post("/ingest/manifest", json=body(service_id=sid), headers=auth)
    mid = r.json()["data"]["manifest_id"]
    async with SessionLocal() as s, s.begin():
        await s.execute(text("DELETE FROM service WHERE service_id = :i"), {"i": sid})
    r = await client.post(f"/ingest/replay/{mid}", headers=auth)
    assert r.status_code == 404
    assert r.json()["error"] == "Service not found"


async def test_ingest_mongo_down_is_503_and_postgres_untouched(client, auth, monkeypatch, reseed):
    monkeypatch.setattr(ingest, "raw_manifests", _DownCollection)
    with mock_upstreams():
        r = await client.post("/ingest/manifest", json=body(), headers=auth)
    assert r.status_code == 503
    assert r.json() == {"data": None, "error": "Raw manifest store unavailable"}
    assert await scalar("SELECT max(deploy_seq) FROM deployment WHERE service_id = 1") == 2
    r = await client.post(f"/ingest/replay/{ObjectId()}", headers=auth)
    assert r.status_code == 503


async def test_ingest_advisory_store_down_is_503_and_marks_failed(
    client, auth, monkeypatch, reseed
):
    monkeypatch.setattr(osv_fetcher, "osv_advisories", _DownCollection)
    with mock_upstreams():
        r = await client.post("/ingest/manifest", json=body(), headers=auth)
    assert r.status_code == 503
    doc = await get_mongo()["raw_manifests"].find_one({"service_id": 1})
    assert doc["status"] == "failed" and "503" in doc["error"]
    assert await scalar("SELECT max(deploy_seq) FROM deployment WHERE service_id = 1") == 2


async def test_status_update_failure_does_not_fail_committed_ingest(
    client, auth, monkeypatch, reseed
):
    real = ingest.raw_manifests

    class StoreOnly:
        def __getattr__(self, name):
            return (
                _DownCollection().__getattr__(name)
                if name == "update_one"
                else getattr(real(), name)
            )

    monkeypatch.setattr(ingest, "raw_manifests", StoreOnly)
    with mock_upstreams():
        r = await client.post("/ingest/manifest", json=body(), headers=auth)
    assert r.status_code == 200
    assert await scalar("SELECT max(deploy_seq) FROM deployment WHERE service_id = 1") == 3
    doc = await get_mongo()["raw_manifests"].find_one({"service_id": 1})
    assert doc["status"] == "received"  # Postgres committed; only the bookkeeping was lost


async def test_scan_degrades_when_mongo_down(client, auth, monkeypatch, reseed):
    before = await scalar("SELECT count(*) FROM scan_run WHERE service_id = 1")
    monkeypatch.setattr(osv_fetcher, "osv_advisories", _DownCollection)
    with mock_upstreams():
        r = await client.post("/ingest/scan/1", headers=auth)
    assert r.status_code == 200
    assert await scalar("SELECT count(*) FROM scan_run WHERE service_id = 1") == before + 1


async def test_store_raw_is_idempotent_upsert():
    adv = {"GHSA-x": {"id": "GHSA-x", "summary": "one"}}
    assert await osv_fetcher.store_raw(adv)
    adv["GHSA-x"]["summary"] = "two"
    assert await osv_fetcher.store_raw(adv)
    coll = get_mongo()["osv_advisories"]
    assert await coll.count_documents({"osv_id": "GHSA-x"}) == 1
    assert (await coll.find_one({"osv_id": "GHSA-x"}))["raw_osv"]["summary"] == "two"
