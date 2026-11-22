"""S5b: unknown is never safe, thresholds from settings, descriptions from registries, honest
LLM-skipped text, depth-cap flag, embedding outage flag. External HTTP is mocked (respx)."""

from decimal import Decimal as D

import pytest
import respx
from pymongo.errors import PyMongoError
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal
from app.routers import clusters as clusters_router
from app.services import maintainers, pr_gate_engine
from app.services.pr_gate_engine import stage2
from tests.upstream import mock_upstreams

CRON = {"X-Cron-Secret": "test-cron-secret"}
DEP = ("pypi", "flask", "9.9.9")  # mock_upstreams: flask has a CRITICAL vuln and a GPL license


async def gate(dep=DEP, service_id=1):
    unverified = await pr_gate_engine.refresh_stale([dep])
    async with SessionLocal() as s, s.begin():
        return (await pr_gate_engine.evaluate_all(s, [dep], service_id, unverified))[0]


# ---- thresholds --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score,want",
    [("6.9", "PASS"), ("7.0", "ADVISORY"), ("8.9", "ADVISORY"), ("9.0", "BLOCK")],
)
def test_stage2_cvss_bands_follow_severity(score, want):
    assert stage2([D(score)], [True], False, 0)[0] == want


def test_stage2_thresholds_come_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "gate_advisory_cvss", 4.0)
    monkeypatch.setattr(settings, "gate_block_cvss", 7.0)
    monkeypatch.setattr(settings, "gate_block_abandonment", 60)
    assert stage2([D("5.0")], [True], False, 0)[0] == "ADVISORY"
    assert stage2([D("7.0")], [True], False, 0)[0] == "BLOCK"
    assert stage2([], [], False, 60)[0] == "BLOCK"


def test_stage2_unverified_is_never_pass():
    verdict, why = stage2([], [], False, None, unverified=["could not verify x: license unknown"])
    assert verdict == "ADVISORY" and "could not verify x" in why


# ---- unknown != safe ---------------------------------------------------------------------------


async def test_unknown_package_is_advisory_when_live_lookup_off(reseed):
    d = await gate(("pypi", "never-heard-of-it", "1.0.0"))
    assert d.verdict == "ADVISORY" and d.stage_reached == 2
    assert "could not verify never-heard-of-it" in d.reasoning
    assert "live lookup is disabled" in d.reasoning


@respx.mock
async def test_live_lookup_decides_unknown_dep_and_stores_it(monkeypatch, reseed):
    monkeypatch.setattr(settings, "gate_live_lookup", True)
    with mock_upstreams():
        d = await gate()
    assert d.verdict == "BLOCK" and "copyleft" in d.reasoning and "CVSS 9.8" in d.reasoning
    async with SessionLocal() as s:
        stored = (
            await s.execute(
                text(
                    "SELECT pv.osv_checked_at IS NOT NULL, pv.license_id IS NOT NULL, "
                    "(SELECT count(*) FROM affects a WHERE a.package_id = pv.package_id "
                    " AND a.version_string = pv.version_string) FROM package_version pv "
                    "JOIN package p USING (package_id) "
                    "WHERE p.ecosystem = 'pypi' AND p.name = 'flask' AND version_string = '9.9.9'"
                )
            )
        ).one()
    assert tuple(stored) == (True, True, 1)
    # now fresh in the DB: no second network round trip
    with respx.mock(assert_all_called=False) as offline:
        assert await pr_gate_engine.refresh_stale([DEP]) == {}
        assert not offline.calls


@respx.mock
async def test_osv_down_means_could_not_verify_not_pass(monkeypatch, reseed):
    monkeypatch.setattr(settings, "gate_live_lookup", True)
    respx.post("https://api.osv.dev/v1/querybatch").respond(503)
    respx.get(url__regex=r"https://api\.deps\.dev/.*").respond(200, json={})
    d = await gate()
    assert d.verdict == "ADVISORY"
    assert "could not verify flask: OSV lookup failed" in d.reasoning


@respx.mock
async def test_deps_dev_down_leaves_license_and_abandonment_unknown(monkeypatch, reseed):
    monkeypatch.setattr(settings, "gate_live_lookup", True)
    with mock_upstreams(deps_dev_status=500):
        d = await gate(("pypi", "somelib", "1.0.0"))
    assert d.verdict == "ADVISORY"
    assert "license unknown" in d.reasoning and "abandonment unknown" in d.reasoning
    assert d.facts["abandonment"] is None and d.facts["copyleft"] is False


async def test_stale_osv_data_is_not_trusted(reseed):
    async with SessionLocal() as s, s.begin():
        await s.execute(text("UPDATE package_version SET osv_checked_at = NULL"))
        pkg = (
            await s.execute(
                text(
                    "SELECT p.ecosystem, p.name, pv.version_string FROM package p "
                    "JOIN package_version pv USING (package_id) "
                    "WHERE pv.license_id IS NOT NULL AND pv.published_at IS NOT NULL LIMIT 1"
                )
            )
        ).one()
    d = await gate(tuple(pkg))
    assert d.verdict == "ADVISORY" and "could not verify" in d.reasoning


# ---- LLM review skipped is stated --------------------------------------------------------------


async def test_no_groq_key_is_stated_in_the_verdict(monkeypatch, reseed):
    monkeypatch.setattr(settings, "groq_api_key", "")
    d = await gate(("pypi", "never-heard-of-it", "1.0.0"))
    verdict, _ = await pr_gate_engine.refine([d], 1, 1, store_responses=False)
    assert (
        verdict == "ADVISORY" and "LLM review skipped (no GROQ_API_KEY configured)" in d.reasoning
    )


@respx.mock
async def test_groq_failure_is_stated_in_the_verdict(monkeypatch, reseed):
    monkeypatch.setattr(settings, "groq_api_key", "k")
    monkeypatch.setattr(pr_gate_engine, "LLM_ATTEMPTS", 1)
    respx.post("https://api.groq.com/openai/v1/chat/completions").respond(500, json={})
    d = await gate(("pypi", "never-heard-of-it", "1.0.0"))
    await pr_gate_engine.refine([d], 1, 1, store_responses=False)
    assert d.stage_reached == 2 and "LLM review skipped (" in d.reasoning
    assert d.reasoning.startswith("could not verify")  # rule-based reason kept


# ---- descriptions from registries ---------------------------------------------------------------


def test_registries_yield_descriptions():
    assert maintainers.parse_npm({"description": " HTTP client "}).description == "HTTP client"
    assert maintainers.parse_pypi({"info": {"summary": "Fast JSON"}}).description == "Fast JSON"
    crate = {"crate": {"description": "Serialization"}}
    assert maintainers.parse_crates({}, crate).description == "Serialization"
    assert maintainers.parse_npm({"description": 5}).description is None


@respx.mock
async def test_maintainers_sync_fills_descriptions_and_rotates(client, monkeypatch, reseed):
    npm = {"maintainers": [{"name": "m1"}], "description": "A real npm description"}
    respx.get(url__regex=r"https://registry\.npmjs\.org/.*").respond(200, json=npm)
    respx.get(url__regex=r"https://pypi\.org/pypi/.*/json").respond(
        200, json={"info": {"maintainer": "p1", "summary": "A real PyPI summary"}}
    )
    respx.get(url__regex=r"https://crates\.io/.*").respond(404)
    monkeypatch.setattr(settings, "registry_sync_batch", 3)
    r = await client.post("/internal/maintainers-sync", headers=CRON)
    assert r.status_code == 200 and r.json()["data"]["packages_scanned"] == 3
    async with SessionLocal() as s:
        done = (
            await s.execute(
                text(
                    "SELECT count(*) FILTER (WHERE description IN "
                    "('A real npm description', 'A real PyPI summary')), "
                    "count(*) FILTER (WHERE registry_synced_at IS NOT NULL) FROM package"
                )
            )
        ).one()
    assert tuple(done)[1] == 3 and 0 < tuple(done)[0] <= 3
    again = await client.post("/internal/maintainers-sync", headers=CRON)  # next 3, not the same
    async with SessionLocal() as s:
        synced = (
            await s.execute(
                text("SELECT count(*) FROM package WHERE registry_synced_at IS NOT NULL")
            )
        ).scalar_one()
    assert again.status_code == 200 and synced >= 5


# ---- depth cap flag ------------------------------------------------------------------------------


@respx.mock
async def test_closure_cut_at_depth_cap_is_flagged(client, auth, monkeypatch, reseed):
    async with SessionLocal() as s, s.begin():
        top = (
            await s.execute(
                text(
                    "SELECT p.name, d.dep_version_string FROM depends_on d "
                    "JOIN package p ON p.package_id = d.dep_package_id "
                    "WHERE p.ecosystem = 'pypi' LIMIT 1"
                )
            )
        ).one()
    body = {"service_id": 1, "ecosystem": "pypi", "filename": "requirements.txt"}
    body["content"] = f"{top[0]}=={top[1]}\n"
    results = {}
    for cap in (0, 10):
        monkeypatch.setattr(settings, "max_transitive_depth", cap)
        with respx.mock(assert_all_called=False) as router:
            router.post("https://api.osv.dev/v1/querybatch").respond(200, json={"results": [{}]})
            router.get(url__regex=r"https://api\.deps\.dev/.*").respond(200, json={})
            r = await client.post("/ingest/manifest", json=body, headers=auth)
        assert r.status_code == 200, r.text
        g = (await client.get("/services/1/graph", headers=auth)).json()["data"]
        results[cap] = g["truncated"]
    assert results[0] is True and results[10] is False
    br = (await client.get("/blast-radius/1", headers=auth)).json()["data"]
    assert br["truncated"] is False


# ---- embedding outage ------------------------------------------------------------------------------


async def test_clusters_say_when_embeddings_are_unavailable(client, auth, monkeypatch, reseed):
    def broken():
        raise PyMongoError("down")

    async with SessionLocal() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO semantic_cluster (label, keywords, member_count) "
                "VALUES ('x / y', ARRAY['x'], 1)"
            )
        )
    monkeypatch.setattr(clusters_router, "get_mongo", broken)
    got = (await client.get("/clusters", headers=auth)).json()["data"]
    assert got and all(c["embeddings_available"] is False for c in got)


# ---- live lookup hardening ---------------------------------------------------------------------


async def affects_count(name="flask") -> int:
    async with SessionLocal() as s:
        return (
            await s.execute(
                text(
                    "SELECT count(*) FROM affects a JOIN package p USING (package_id) "
                    "WHERE p.name = :n AND a.version_string = '9.9.9'"
                ),
                {"n": name},
            )
        ).scalar_one()


@respx.mock
async def test_unsupported_ecosystem_does_not_poison_the_batch(monkeypatch, reseed):
    monkeypatch.setattr(settings, "gate_live_lookup", True)
    odd = ("maven", "x", "1")
    with mock_upstreams():
        out = await pr_gate_engine.refresh_stale([odd, DEP])
    assert out == {odd: "unsupported ecosystem"} and await affects_count() == 1


@respx.mock
async def test_live_lookup_cap_reports_the_rest_and_keeps_the_finished(monkeypatch, reseed):
    monkeypatch.setattr(settings, "gate_live_lookup", True)
    monkeypatch.setattr(settings, "gate_max_live_lookups", 1)
    other = ("pypi", "zzz", "1.0.0")
    with mock_upstreams():
        out = await pr_gate_engine.refresh_stale([DEP, other])
    assert out == {other: "lookup cap reached"} and await affects_count() == 1


@respx.mock
async def test_deps_dev_stall_keeps_osv_results(monkeypatch, reseed):
    monkeypatch.setattr(settings, "gate_live_lookup", True)

    async def stall(refs):
        raise TimeoutError

    monkeypatch.setattr(pr_gate_engine, "fetch_metadata", stall)
    with mock_upstreams():
        out = await pr_gate_engine.refresh_stale([DEP])
    assert out == {DEP: "deps.dev lookup failed (TimeoutError)"}
    assert await affects_count() == 1  # OSV side stored


@respx.mock
async def test_any_osv_error_fails_closed_without_touching_affects(monkeypatch, reseed):
    monkeypatch.setattr(settings, "gate_live_lookup", True)
    with mock_upstreams():
        await pr_gate_engine.refresh_stale([DEP])
    async with SessionLocal() as s, s.begin():
        await s.execute(text("UPDATE package_version SET osv_checked_at = NULL"))

    async def boom(refs):
        raise RuntimeError("bad")

    monkeypatch.setattr(pr_gate_engine.osv_fetcher, "fetch_advisories", boom)
    with mock_upstreams():
        out = await pr_gate_engine.refresh_stale([DEP])
    assert out == {DEP: "OSV lookup failed (RuntimeError)"} and await affects_count() == 1


@respx.mock
async def test_unstorable_lookup_is_unverified_not_raised(monkeypatch, reseed):
    monkeypatch.setattr(settings, "gate_live_lookup", True)

    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(pr_gate_engine, "upsert_packages", boom)
    with mock_upstreams():
        out = await pr_gate_engine.refresh_stale([DEP])
    assert out == {DEP: "could not store lookup result"}


@respx.mock
async def test_slow_mongo_does_not_delay_or_change_the_verdict(monkeypatch, reseed):
    monkeypatch.setattr(settings, "gate_live_lookup", True)
    monkeypatch.setattr(pr_gate_engine, "RAW_STORE_TIMEOUT", 0.05)

    async def slow(advisories):
        await pr_gate_engine.asyncio.sleep(5)

    monkeypatch.setattr(pr_gate_engine.osv_fetcher, "store_raw", slow)
    with mock_upstreams():
        d = await gate()
    assert d.verdict == "BLOCK" and "CVSS 9.8" in d.reasoning


@respx.mock
async def test_stage3_skips_unverified_only_findings(monkeypatch, reseed):
    monkeypatch.setattr(settings, "groq_api_key", "k")
    route = respx.post("https://api.groq.com/openai/v1/chat/completions").respond(
        200, json={"choices": [{"message": {"content": '{"verdict":"BLOCK","reasoning":"x"}'}}]}
    )
    d = await gate(("pypi", "never-heard-of-it", "1.0.0"))
    await pr_gate_engine.refine([d], 1, 1, store_responses=False)
    assert d.verdict == "ADVISORY" and d.stage_reached == 2 and not route.called
    assert "LLM review skipped" in d.reasoning


@respx.mock
async def test_deps_dev_gap_is_re_asked_only_after_the_ttl(monkeypatch, reseed):
    monkeypatch.setattr(settings, "gate_live_lookup", True)
    respx.post("https://api.osv.dev/v1/querybatch").respond(200, json={"results": [{}]})
    dd = respx.get(url__regex=r"https://api\.deps\.dev/.*").respond(200, json={})
    dep = ("pypi", "nolicense", "1.0.0")
    await pr_gate_engine.refresh_stale([dep])
    await pr_gate_engine.refresh_stale([dep])  # license/published_at still NULL, but checked
    assert dd.call_count == 1
    async with SessionLocal() as s, s.begin():
        await s.execute(
            text("UPDATE package_version SET deps_dev_checked_at = now() - interval '100 hours'")
        )
    await pr_gate_engine.refresh_stale([dep])
    assert dd.call_count == 2
