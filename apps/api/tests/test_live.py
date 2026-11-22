"""Opt-in proof that the real integrations work (DEPHGRAPH_LIVE=1): OSV, deps.dev, npm, PyPI and
crates.io answer for known packages, and the PR gate's live path decides with their data.
No mocks, real network; skipped otherwise (see conftest)."""

import httpx
import pytest

from app.config import settings
from app.database import SessionLocal
from app.services import maintainers, osv_fetcher, pr_gate_engine
from app.services.deps_dev_client import fetch_metadata
from app.services.manifest_parser import PkgRef

pytestmark = pytest.mark.live
REQUESTS = PkgRef(0, "pypi", "requests", "2.19.0")  # old release with many published advisories


async def test_osv_knows_vulns_of_requests_2_19_0():
    hits, advisories = await osv_fetcher.fetch_advisories([REQUESTS])
    assert hits[REQUESTS], "OSV returned no advisory for requests 2.19.0"
    osv_id = hits[REQUESTS][0]
    severity, score = osv_fetcher.classify(advisories[osv_id])
    assert severity in {"CRITICAL", "HIGH", "MEDIUM", "LOW"} or score is None


async def test_deps_dev_returns_license_and_publish_date():
    meta = await fetch_metadata([REQUESTS])
    assert REQUESTS in meta, "deps.dev gave nothing for requests 2.19.0"
    assert meta[REQUESTS].license and meta[REQUESTS].published_at


async def test_registries_return_maintainers_and_descriptions():
    headers = {"User-Agent": maintainers.USER_AGENT}
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        for eco, name in (("npm", "left-pad"), ("pypi", "requests"), ("cargo", "serde")):
            found = await maintainers.fetch_one(client, eco, name)
            assert found is not None, f"{eco}:{name} lookup failed"
            if eco != "pypi":  # PyPI's JSON API lists no account owners; often no author either
                assert found.people, f"{eco}:{name} has no maintainers"
            assert found.description, f"{eco}:{name} has no description"
            assert found.last_active_at is not None, f"{eco}:{name} has no activity date"


async def test_gate_decides_unknown_dependency_from_live_data(monkeypatch, reseed):
    monkeypatch.setattr(settings, "gate_live_lookup", True)
    dep = ("pypi", "requests", "2.19.0")
    unverified = await pr_gate_engine.refresh_stale([dep])
    assert unverified == {}, unverified
    async with SessionLocal() as session, session.begin():
        verdict = (await pr_gate_engine.evaluate_all(session, [dep], 1, unverified))[0]
    assert verdict.stage_reached == 2 and verdict.facts["vulns"], verdict
    assert verdict.verdict in {"ADVISORY", "BLOCK"}, verdict
