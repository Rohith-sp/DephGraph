from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from app.database import SessionLocal, get_mongo, get_redis
from app.models.mongo import ensure_indexes
from app.services import blast_radius
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
