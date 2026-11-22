"""Regressions for the area-A review (each fails on the pre-fix code)."""

import base64
import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
import respx
import structlog.testing
from pymongo.errors import PyMongoError
from sqlalchemy import text

from app import auth, main
from app.auth import Member
from app.config import settings
from app.database import SessionLocal, get_mongo
from app.routers.ingest import _upsert_packages
from app.schemas.runtime import RuntimeSamplesRequest
from app.services import cache, deps_dev_client, maintainers, osv_fetcher, runtime
from app.services.manifest_parser import PkgRef, parse_requirements
from app.services.queries import PRUNABLE_SQL, rows
from tests.conftest import DEV_USER
from tests.test_auth import JWKS_URL, _keypair
from tests.test_ingest import body, scalar
from tests.test_runtime import fetch, payload, resolved
from tests.upstream import mock_upstreams


def _b64(obj: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def _forged(header: dict) -> dict[str, str]:
    claims = {"sub": "u1", "aud": "authenticated", "exp": int(time.time()) + 600}
    return {"Authorization": f"Bearer {_b64(header)}.{_b64(claims)}.c2ln"}


@pytest.fixture
def jwks(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "http://supabase.test")
    monkeypatch.setattr(auth, "_jwks", {})
    monkeypatch.setattr(auth, "_jwks_fetched", 0.0)
    pem, pub = _keypair("k1")
    with respx.mock:
        respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json={"keys": [pub]}))
        yield


# A1 / A10 ---------------------------------------------------------------------------------------
async def test_service_without_deployment_has_zero_packages(client, auth, reseed):
    async with SessionLocal() as s, s.begin():
        sid = (
            await s.execute(
                text(
                    "INSERT INTO service (team_id, name, language) "
                    "VALUES (1, 'never-ingested', 'python') RETURNING service_id"
                )
            )
        ).scalar_one()
    svc = next(
        x
        for x in (await client.get("/services", headers=auth)).json()["data"]
        if x["service_id"] == sid
    )
    assert (svc["package_count"], svc["vuln_count"], svc["critical_count"]) == (0, 0, 0)


async def test_service_list_counts_honour_waivers_like_overview(client, auth, reseed):
    async def vulns() -> tuple[int, int]:
        services = (await client.get("/services", headers=auth)).json()["data"]
        overview = (await client.get("/stats/overview", headers=auth)).json()["data"]
        assert overview["open_vulns"] <= sum(x["vuln_count"] for x in services)
        return (
            next(x["vuln_count"] for x in services if x["service_id"] == 1),
            overview["open_vulns"],
        )

    before, _ = await vulns()
    assert before > 0
    async with SessionLocal() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO waived_for (vuln_id, service_id, reason) "
                "SELECT DISTINCT a.vuln_id, 1, 'accepted' FROM affects a "
                "JOIN resolves_to r USING (package_id, version_string) WHERE r.service_id = 1 "
                "ON CONFLICT (vuln_id, service_id) DO UPDATE SET expires_on = NULL"
            )
        )
    await cache.bump()  # direct SQL, not an API write
    assert (await vulns())[0] == 0


# A2 / A3 + auth edge cases ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "header",
    [
        {"alg": "RS256", "kid": "k1"},  # alg does not match the EC JWK -> JWKError
        {"alg": "ES256", "kid": [1]},  # unhashable kid -> TypeError
        {"alg": "ES256", "kid": {"a": 1}},
        {"alg": "ES256", "kid": 7},
        {"alg": "ES256"},  # missing kid
        {"alg": "ES256", "kid": "nope"},
        {"alg": "none", "kid": "k1"},
        {"alg": "HS512"},
        {"alg": ["ES256"], "kid": "k1"},
        {"kid": "k1"},  # no alg
    ],
)
async def test_hostile_token_headers_are_401_never_500(client, jwks, header):
    r = await client.get("/services", headers=_forged(header))
    assert r.status_code == 401
    assert r.json() == {"data": None, "error": "Invalid token"}


@pytest.mark.parametrize(
    "token",
    ["", "garbage", "a.b.c", "....", "e30.e30.e30", "%%%.%%%.%%%", "x" * 5000],
)
async def test_garbage_bearer_is_401(client, jwks, token):
    r = await client.get("/services", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


@pytest.mark.parametrize("scheme", ["Basic abc", "Token abc", "Bearer", "bearer  "])
async def test_wrong_auth_scheme_is_401(client, scheme):
    assert (await client.get("/services", headers={"Authorization": scheme})).status_code == 401


async def test_hs256_wrong_secret_wrong_aud_expired_are_401(client):
    import jwt

    def tok(secret="test-jwt-secret-at-least-32-characters", aud="authenticated", exp=600):
        claims = {
            "sub": "00000000-0000-0000-0000-000000000001",
            "aud": aud,
            "exp": int(time.time()) + exp,
        }
        return {"Authorization": "Bearer " + jwt.encode(claims, secret, algorithm="HS256")}

    for h in (tok(secret="x" * 40), tok(aud="anon"), tok(exp=-60)):
        assert (await client.get("/services", headers=h)).status_code == 401
    assert (await client.get("/services", headers=tok())).status_code == 200


async def test_forged_header_does_not_bypass_rate_limit_500(client, jwks, monkeypatch):
    from app.limiter import storage

    monkeypatch.setattr(settings, "rate_limit", "2/minute")
    storage.reset()
    h = _forged({"alg": "RS256", "kid": "k1"})
    codes = [(await client.get("/services", headers=h)).status_code for _ in range(3)]
    storage.reset()
    assert codes == [401, 401, 429]


# A4 ---------------------------------------------------------------------------------------------
async def test_wrong_ecosystem_upload_does_not_poison_same_content(client, auth, reseed):
    wrong = await client.post("/ingest/manifest", json=body(ecosystem="npm"), headers=auth)
    assert wrong.status_code == 422
    with mock_upstreams():
        right = await client.post("/ingest/manifest", json=body(), headers=auth)
    assert right.status_code == 200, right.text
    doc = await get_mongo()["raw_manifests"].find_one({})
    assert (doc["ecosystem"], doc["filename"]) == ("pypi", "requirements.txt")
    assert await get_mongo()["raw_manifests"].count_documents({}) == 1


# A5 ---------------------------------------------------------------------------------------------
async def test_overview_prunable_counts_service_package_pairs(client, auth, reseed):
    """Same package, same deploy_seq in two services: live in one, dead in the other."""
    async with SessionLocal() as s, s.begin():
        pid = (
            await s.execute(
                text(
                    "INSERT INTO package (ecosystem, name) VALUES ('pypi', 'shared-x') RETURNING package_id"
                )
            )
        ).scalar_one()
        await s.execute(
            text("INSERT INTO package_version (package_id, version_string) VALUES (:p, '1.0')"),
            {"p": pid},
        )
        for sid, live in ((2, True), (3, False)):
            scan = (
                await s.execute(
                    text(
                        "INSERT INTO scan_run (service_id, completed_at, source) "
                        "VALUES (:s, now(), 'cli') RETURNING scan_id"
                    ),
                    {"s": sid},
                )
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO resolves_to (service_id, deploy_seq, package_id, version_string, "
                    "depth, is_direct) VALUES (:s, 1, :p, '1.0', 0, true)"
                ),
                {"s": sid, "p": pid},
            )
            await s.execute(
                text(
                    "INSERT INTO observes (service_id, deploy_seq, scan_id, package_id, "
                    "version_string, exec_count, is_live) VALUES (:s, 1, :c, :p, '1.0', :n, :l)"
                ),
                {"s": sid, "c": scan, "p": pid, "n": int(live), "l": live},
            )
    async with SessionLocal() as s:
        overall = await rows(
            s,
            f"SELECT service_id, name FROM ({PRUNABLE_SQL}) p",
            sid=None,
            teams=[1, 2, 3],
        )
    per_service = [
        (await client.get(f"/services/{i}/prunable", headers=auth)).json()["data"]
        for i in range(1, 6)
    ]
    assert len(overall) == sum(map(len, per_service))
    assert [r["service_id"] for r in overall if r["name"] == "shared-x"] == [3]
    assert (await client.get("/stats/overview", headers=auth)).json()["data"][
        "prunable_packages"
    ] == len(overall)


# A6 / A7 ----------------------------------------------------------------------------------------
async def test_affects_upserted_in_sorted_order(monkeypatch):
    seen: list[list[dict]] = []
    real = osv_fetcher.chunks
    monkeypatch.setattr(osv_fetcher, "chunks", lambda items: seen.append(items) or real(items))
    adv = {"X-1": {"id": "X-1"}, "X-2": {"id": "X-2"}}
    async with SessionLocal() as s, s.begin():
        pv = (
            await s.execute(
                text(
                    "SELECT package_id, version_string FROM package_version ORDER BY 1 DESC LIMIT 3"
                )
            )
        ).all()
        refs = [PkgRef(p, "pypi", f"n{p}", v) for p, v in pv]
        hits = {refs[0]: ["X-2", "X-1"], refs[2]: ["X-1"], refs[1]: ["X-2"]}
        await osv_fetcher.upsert_advisories(s, refs, hits, adv)
        await s.rollback()
    keys = [(a["package_id"], a["version_string"], a["vuln_id"]) for a in seen[-1]]
    assert len(keys) == 4 and keys == sorted(keys)


async def test_upsert_packages_survives_more_than_32767_bind_params(reseed):
    names = [f"bulk-{i:05d}" for i in range(17_000)]  # 2 params per row = 34000 in one statement
    parsed = [SimpleNamespace(name=n, version="1.0") for n in names]
    async with SessionLocal() as s, s.begin():
        refs, inserted = await _upsert_packages(s, "pypi", parsed)
    assert len(refs) == inserted == 17_000
    async with SessionLocal() as s, s.begin():
        _, again = await _upsert_packages(s, "pypi", parsed)
    assert again == 0


async def test_maintainers_upsert_survives_more_than_32767_bind_params(reseed):
    people = tuple(maintainers.Person(f"user{i}", None) for i in range(9_000))  # 4 params each
    async with SessionLocal() as s, s.begin():
        pid = (await s.execute(text("SELECT min(package_id) FROM package"))).scalar_one()
        n = await maintainers.upsert(s, {pid: maintainers.PackageMaintainers("npm", people, None)})
    assert n == 9_000
    assert await scalar("SELECT count(*) FROM maintained_by WHERE package_id = :p", p=pid) == 9_000


# A11 --------------------------------------------------------------------------------------------
async def test_rescan_removes_affects_osv_no_longer_reports(client, auth, reseed):
    with mock_upstreams():
        assert (await client.post("/ingest/manifest", json=body(), headers=auth)).status_code == 200
    count = "SELECT count(*) FROM affects a JOIN vulnerability v USING (vuln_id) WHERE v.osv_id = 'GHSA-test-0001'"
    assert await scalar(count) >= 1
    router = mock_upstreams()
    router.post("https://api.osv.dev/v1/querybatch").mock(
        side_effect=lambda req: httpx.Response(
            200, json={"results": [{}] * len(json.loads(req.content)["queries"])}
        )
    )
    with router:
        assert (await client.post("/ingest/scan/1", headers=auth)).status_code == 200
    assert await scalar(count) == 0
    # other (seeded) advisories on packages that were not rescanned stay
    assert await scalar("SELECT count(*) FROM affects") > 0


# A12 --------------------------------------------------------------------------------------------
async def test_store_raw_is_one_bulk_write(monkeypatch):
    calls = []

    class Coll:
        async def bulk_write(self, ops, ordered):
            calls.append((len(ops), ordered))

    monkeypatch.setattr(osv_fetcher, "osv_advisories", lambda: Coll())
    assert await osv_fetcher.store_raw({f"X-{i}": {} for i in range(25)})
    assert calls == [(25, False)]
    assert await osv_fetcher.store_raw({})
    assert calls == [(25, False)]  # nothing to write -> no round trip


async def test_store_raw_bulk_failure_is_reported(monkeypatch):
    class Coll:
        async def bulk_write(self, ops, ordered):
            raise PyMongoError("down")

    monkeypatch.setattr(osv_fetcher, "osv_advisories", lambda: Coll())
    assert not await osv_fetcher.store_raw({"X": {}})
    with pytest.raises(PyMongoError):
        await osv_fetcher.store_raw({"X": {}}, required=True)


# A13 --------------------------------------------------------------------------------------------
async def test_apply_metadata_batches_and_keeps_existing_values(reseed):
    async with SessionLocal() as s:
        pv = (
            await s.execute(
                text("SELECT package_id, version_string FROM package_version ORDER BY 1, 2 LIMIT 3")
            )
        ).all()
    refs = [PkgRef(p, "pypi", "n", v) for p, v in pv]
    when = datetime(2020, 1, 1, tzinfo=UTC)
    meta = {
        refs[0]: deps_dev_client.Metadata("MIT", when, True),
        refs[1]: deps_dev_client.Metadata(None, None, False),  # unknown license/date: keep old
    }
    async with SessionLocal() as s, s.begin():
        before = (
            await s.execute(
                text(
                    "SELECT license_id, published_at FROM package_version WHERE (package_id, version_string) = (:p, :v)"
                ),
                {"p": refs[1].package_id, "v": refs[1].version},
            )
        ).one()
        await deps_dev_client.apply_metadata(s, meta)
        await deps_dev_client.apply_metadata(s, {})  # no-op, no empty executemany
    row = await fetch(
        "SELECT l.spdx_id, pv.published_at, pv.is_yanked FROM package_version pv JOIN license l USING (license_id) WHERE (pv.package_id, pv.version_string) = (:p, :v)",
        p=refs[0].package_id,
        v=refs[0].version,
    )
    assert row[0]["spdx_id"] == "MIT" and row[0]["is_yanked"] and row[0]["published_at"] == when
    after = await fetch(
        "SELECT license_id, published_at FROM package_version WHERE (package_id, version_string) = (:p, :v)",
        p=refs[1].package_id,
        v=refs[1].version,
    )
    assert (after[0]["license_id"], after[0]["published_at"]) == tuple(before)


# A14 --------------------------------------------------------------------------------------------
def test_crlf_requirements_with_hash_continuations():
    content = (
        "flask==2.0.1 \\\r\n    --hash=sha256:aaa \\\r\n    --hash=sha256:bbb\r\n"
        "requests==2.31.0 \\\r\n    --hash=sha256:ccc\r\n"
    )
    assert [(p.name, p.version) for p in parse_requirements(content)] == [
        ("flask", "2.0.1"),
        ("requests", "2.31.0"),
    ]


# A15 / A16 --------------------------------------------------------------------------------------
async def test_runtime_samples_match_pypi_names_after_pep503_normalisation(client, auth, reseed):
    pkg = next(p for p in await resolved(1, 1) if p["name"] != p["name"].upper())
    sample = {
        "name": pkg["name"].upper().replace("-", "_"),
        "version": pkg["version"],
        "exec_count": 4,
    }
    r = await client.post("/runtime/samples", json=payload([sample]), headers=auth)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert (d["observes_written"], d["live_packages"], d["samples_skipped"]) == (
        1,
        1,
        0,
    )


async def test_db_error_marks_raw_sample_doc_failed(monkeypatch, reseed):
    async def boom(*a, **k):
        raise RuntimeError("deadlock detected")

    monkeypatch.setattr(runtime, "_project", boom)
    req = RuntimeSamplesRequest.model_validate(
        payload([{"name": "x", "version": "1", "exec_count": 1}])
    )
    async with SessionLocal() as s:
        with pytest.raises(RuntimeError):
            await runtime.record_samples(s, req, Member(DEV_USER, frozenset({1})))
    doc = await runtime.runtime_samples().find_one({}, sort=[("_id", -1)])
    assert doc["status"] == "failed" and "deadlock" in doc["error"]


# A21 / A23 --------------------------------------------------------------------------------------
async def test_preload_failure_is_logged():
    import asyncio

    async def fail():
        raise RuntimeError("no model")

    t = asyncio.create_task(fail())
    await asyncio.wait([t])
    with structlog.testing.capture_logs() as logs:
        main._log_preload(t)
    assert [(e["event"], e["log_level"]) for e in logs] == [("model_preload_failed", "error")]


async def test_overview_no_longer_exposes_unused_copyleft_field(client, auth):
    assert (
        "copyleft_packages"
        not in (await client.get("/stats/overview", headers=auth)).json()["data"]
    )


# A11 follow-up: querybatch pagination ------------------------------------------------------------
def _paged_osv(pages: dict[str | None, dict], seen: list):
    def handler(req: httpx.Request) -> httpx.Response:
        qs = json.loads(req.content)["queries"]
        seen.append([q.get("page_token") for q in qs])
        return httpx.Response(200, json={"results": [pages[q.get("page_token")] for q in qs]})

    return handler


@respx.mock
async def test_querybatch_follows_next_page_token():
    seen: list = []
    respx.post("https://api.osv.dev/v1/querybatch").mock(
        side_effect=_paged_osv(
            {
                None: {"vulns": [{"id": "X-1"}], "next_page_token": "t2"},
                "t2": {"vulns": [{"id": "X-2"}]},
            },
            seen,
        )
    )
    for i in ("X-1", "X-2"):
        respx.get(f"https://api.osv.dev/v1/vulns/{i}").respond(200, json={"id": i})
    ref = PkgRef(1, "pypi", "flask", "1.0")
    hits, adv = await osv_fetcher.fetch_advisories([ref])
    assert hits == {ref: ["X-1", "X-2"]} and set(adv) == {"X-1", "X-2"}
    assert seen == [[None], ["t2"]]  # page 2 re-queries only the ref that had a token


@respx.mock
async def test_endless_pagination_fails_instead_of_truncating(monkeypatch):
    monkeypatch.setattr(osv_fetcher, "MAX_PAGES", 2)
    respx.post("https://api.osv.dev/v1/querybatch").mock(
        side_effect=lambda req: httpx.Response(
            200, json={"results": [{"vulns": [], "next_page_token": "again"}]}
        )
    )
    with pytest.raises(httpx.HTTPError):
        await osv_fetcher.fetch_advisories([PkgRef(1, "pypi", "flask", "1.0")])


async def test_paged_rescan_keeps_page_two_vulns_and_error_deletes_nothing(client, auth, reseed):
    with mock_upstreams():
        assert (await client.post("/ingest/manifest", json=body(), headers=auth)).status_code == 200
    count = "SELECT count(*) FROM affects"
    before = await scalar(count)
    seen: list = []
    router = mock_upstreams()
    router.post("https://api.osv.dev/v1/querybatch").mock(
        side_effect=lambda req: httpx.Response(
            200,
            json={
                "results": [
                    (
                        {"vulns": [], "next_page_token": "p2"}
                        if q["package"]["name"] == "flask" and "page_token" not in q
                        else (
                            {"vulns": [{"id": "GHSA-test-0001"}]}
                            if q["package"]["name"] == "flask"
                            else {}
                        )
                    )
                    for q in json.loads(req.content)["queries"]
                ]
            },
        )
    )
    with router:  # the advisory only appears on page 2
        assert (await client.post("/ingest/scan/1", headers=auth)).status_code == 200
    assert await scalar(count) == before
    router = mock_upstreams()
    router.post("https://api.osv.dev/v1/querybatch").respond(500)
    with router:  # OSV error mid-scan: nothing is deleted
        assert (await client.post("/ingest/scan/1", headers=auth)).status_code == 502
    assert await scalar(count) == before
