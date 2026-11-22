"""Maintainer sync (npm / PyPI / crates.io, mocked with respx) and the 007 v_abandonment formula."""

from datetime import UTC, datetime, timedelta

import httpx
import respx
from sqlalchemy import text

from app.database import SessionLocal
from app.services import maintainers, pr_gate_engine
from app.services.maintainers import PackageMaintainers, Person

CRON = {"X-Cron-Secret": "test-cron-secret"}
NOW = datetime.now(UTC)
YEAR = 31557600

NPM_DOC = {
    "name": "left-pad",
    "maintainers": [
        {"name": "azer", "email": "x@example.org"},
        {"name": "azer"},
        {"bad": 1},
    ],
    "time": {
        "created": "2014-03-14T00:00:00.000Z",
        "modified": "2023-05-01T12:00:00.000Z",
    },
}
PYPI_DOC = {
    "info": {"maintainer": "", "author": "Alice, Bob ,  "},
    "urls": [
        {"upload_time_iso_8601": "2024-01-02T03:04:05.000000Z"},
        {"upload_time_iso_8601": "2024-02-02T03:04:05.000000Z"},
    ],
}
OWNERS_DOC = {
    "users": [
        {"login": "dtolnay", "kind": "user", "url": "https://github.com/dtolnay"},
        {
            "login": "github:serde-rs:publish",
            "kind": "team",
            "url": "https://github.com/serde-rs",
        },
    ]
}
CRATE_DOC = {"crate": {"name": "serde", "updated_at": "2025-06-01T10:00:00.123456+00:00"}}


# ---- parsing ------------------------------------------------------------------------------


def test_parse_npm():
    m = maintainers.parse_npm(NPM_DOC)
    assert m.platform == "npm"
    assert [p.username for p in m.people] == ["azer"]  # deduped, junk entry skipped
    assert m.people[0].profile_url == "https://www.npmjs.com/~azer"
    assert m.last_active_at == datetime(2023, 5, 1, 12, tzinfo=UTC)


def test_parse_pypi_author_fallback_and_latest_upload():
    m = maintainers.parse_pypi(PYPI_DOC)
    assert m.platform == "pypi"
    assert [(p.username, p.role) for p in m.people] == [
        ("Alice", "author"),
        ("Bob", "author"),
    ]
    assert m.last_active_at == datetime(2024, 2, 2, 3, 4, 5, tzinfo=UTC)


def test_parse_pypi_maintainer_and_release_history():
    doc = {
        "info": {"maintainer": "Carol", "author": "Someone Else"},
        "urls": [],
        "releases": {
            "1.0": [{"upload_time_iso_8601": "2019-01-01T00:00:00Z"}],
            "1.1": [
                {"upload_time_iso_8601": "2020-06-01T00:00:00Z"},
                {"upload_time_iso_8601": "?"},
            ],
            "2.0": None,
        },
    }
    m = maintainers.parse_pypi(doc)
    assert [(p.username, p.role) for p in m.people] == [("Carol", "maintainer")]
    assert m.last_active_at == datetime(2020, 6, 1, tzinfo=UTC)
    empty = maintainers.parse_pypi({"info": {"maintainer": None, "author": None}})
    assert empty.people == () and empty.last_active_at is None


def test_parse_crates():
    m = maintainers.parse_crates(OWNERS_DOC, CRATE_DOC)
    assert m.platform == "crates.io"
    assert [(p.username, p.role) for p in m.people] == [
        ("dtolnay", "owner"),
        ("github:serde-rs:publish", "team"),
    ]
    assert m.last_active_at == datetime(2025, 6, 1, 10, 0, 0, 123456, tzinfo=UTC)
    assert maintainers.parse_crates(OWNERS_DOC, {}).last_active_at is None


# ---- fetching -----------------------------------------------------------------------------


@respx.mock
async def test_fetch_one_per_registry():
    npm = respx.get("https://registry.npmjs.org/@types%2Fnode").respond(200, json=NPM_DOC)
    respx.get("https://pypi.org/pypi/requests/json").respond(200, json=PYPI_DOC)
    owners = respx.get("https://crates.io/api/v1/crates/serde/owners").respond(200, json=OWNERS_DOC)
    respx.get("https://crates.io/api/v1/crates/serde").respond(200, json=CRATE_DOC)
    respx.get("https://pypi.org/pypi/missing/json").respond(404)
    respx.get("https://registry.npmjs.org/broken").respond(200, content=b"not json")

    async with httpx.AsyncClient(headers={"User-Agent": maintainers.USER_AGENT}) as c:
        assert (await maintainers.fetch_one(c, "npm", "@types/node")).platform == "npm"
        assert (await maintainers.fetch_one(c, "pypi", "requests")).people[0].username == "Alice"
        crate = await maintainers.fetch_one(c, "cargo", "serde")
        assert crate.last_active_at.year == 2025 and len(crate.people) == 2
        assert await maintainers.fetch_one(c, "pypi", "missing") is None
        assert await maintainers.fetch_one(c, "npm", "broken") is None
        assert await maintainers.fetch_one(c, "go", "github.com/x/y") is None  # skipped, no call
    assert npm.called
    ua = owners.calls.last.request.headers["User-Agent"]
    assert ua.startswith("dephgraph-course-project") and "@" not in ua  # generic, no personal info


@respx.mock
async def test_fetch_all_skips_unsupported_and_failures():
    respx.get("https://registry.npmjs.org/left-pad").respond(200, json=NPM_DOC)
    respx.get("https://registry.npmjs.org/gone").respond(503)
    got = await maintainers.fetch_all(
        [(1, "npm", "left-pad"), (2, "npm", "gone"), (3, "go", "golang.org/x/net")]
    )
    assert list(got) == [1]


# ---- DB helpers ---------------------------------------------------------------------------


async def mkpkg(name: str, age: float = 0, yanked: bool = False) -> int:
    async with SessionLocal() as s, s.begin():
        pid = (
            await s.execute(
                text(
                    "INSERT INTO package (ecosystem, name) VALUES ('pypi', :n) RETURNING package_id"
                ),
                {"n": name},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO package_version (package_id, version_string, license_id, published_at, "
                "is_yanked, osv_checked_at) "
                "VALUES (:p, '1.0.0', 1, now() - make_interval(secs => :age), :y, now())"
            ),
            {"p": pid, "age": age, "y": yanked},
        )
    return pid


async def score(name: str) -> int:
    async with SessionLocal() as s:
        return (
            await s.execute(
                text("SELECT abandonment_score FROM v_abandonment WHERE name = :n"),
                {"n": name},
            )
        ).scalar_one()


async def sync(found: dict[int, PackageMaintainers]) -> int:
    async with SessionLocal() as s, s.begin():
        return await maintainers.upsert(s, found)


def pm(*names: str, days_idle: float | None) -> PackageMaintainers:
    when = None if days_idle is None else NOW - timedelta(days=days_idle)
    return PackageMaintainers("pypi", tuple(Person(n, None) for n in names), when)


# ---- upsert -------------------------------------------------------------------------------


async def test_upsert_links_and_keeps_newest_activity():
    a, b = await mkpkg("ups-a"), await mkpkg("ups-b")
    assert (
        await sync({a: pm("ups-ann", "ups-ben", days_idle=10), b: pm("ups-ann", days_idle=400)})
        == 2
    )
    # older activity seen later must not move ann's last_active_at backwards; ben dropped from a
    assert await sync({a: pm("ups-ann", days_idle=900), b: pm(days_idle=1)}) == 1
    async with SessionLocal() as s:
        links = (
            await s.execute(
                text(
                    "SELECT mb.package_id, m.username, m.last_active_at, mb.role FROM maintained_by mb "
                    "JOIN maintainer m USING (maintainer_id) WHERE mb.package_id IN (:a, :b) "
                    "ORDER BY 1, 2"
                ),
                {"a": a, "b": b},
            )
        ).all()
    # b had no people in the second sync -> its existing link is left alone
    assert [(p, u, r) for p, u, _, r in links] == [
        (a, "ups-ann", "owner"),
        (b, "ups-ann", "owner"),
    ]
    assert abs((NOW - links[0][2]).days - 10) <= 1
    assert await sync({}) == 0


# ---- v_abandonment (migration 007) --------------------------------------------------------


async def test_view_single_inactive_maintainer_is_high():
    """1 maintainer, idle 3 years: 50 + min(1096, 730)/14.6 = 50 + 50 = 100 -> BLOCK."""
    pid = await mkpkg("ab-single", age=0)  # fresh release: publish age no longer matters
    await sync({pid: pm("ab-solo", days_idle=3 * 365)})
    assert await score("ab-single") == 100
    async with SessionLocal() as s, s.begin():
        d = await pr_gate_engine.evaluate(s, "pypi", "ab-single", "1.0.0")
    assert (d.stage_reached, d.verdict) == (2, "BLOCK")


async def test_view_single_recent_maintainer_is_advisory():
    """1 maintainer active 146 days ago: 50 + 146/14.6 = 60 -> ADVISORY."""
    pid = await mkpkg("ab-single-recent")
    await sync({pid: pm("ab-solo2", days_idle=146)})
    assert await score("ab-single-recent") == 60


async def test_view_several_active_maintainers_is_low():
    """3 maintainers, newest activity 30 days ago: 0 + 30/14.6 = 2.05 -> 2, even if old release."""
    pid = await mkpkg("ab-team", age=5 * YEAR)
    await sync({pid: pm("ab-m1", "ab-m2", "ab-m3", days_idle=30)})
    assert await score("ab-team") == 2
    async with SessionLocal() as s, s.begin():
        d = await pr_gate_engine.evaluate(s, "pypi", "ab-team", "1.0.0")
    assert d.verdict == "PASS"


async def test_view_yanked_adds_40():
    pid = await mkpkg("ab-yanked", yanked=True)
    await sync({pid: pm("ab-y1", "ab-y2", days_idle=0)})
    assert await score("ab-yanked") == 40


async def test_view_fallback_without_maintainer_data():
    """No maintainers -> 002 proxy: publish age * 15/yr (2 years -> 30), not flagged as single."""
    await mkpkg("ab-none", age=2 * YEAR)
    assert await score("ab-none") == 30
    # maintainers known but no activity date (e.g. seed rows) -> same proxy, minus 8 per maintainer
    pid = await mkpkg("ab-noact", age=2 * YEAR)
    await sync({pid: pm("ab-q1", "ab-q2", days_idle=None)})
    assert await score("ab-noact") == 14
    # unknown publish date -> 50 (unchanged from 002)
    async with SessionLocal() as s, s.begin():
        await s.execute(
            text("UPDATE package_version SET published_at = NULL WHERE package_id = :p"),
            {"p": pid},
        )
    assert await score("ab-noact") == 34


# ---- endpoint -----------------------------------------------------------------------------


async def test_maintainers_sync_rejects_bad_secret(client):
    r = await client.post("/internal/maintainers-sync", headers={"X-Cron-Secret": "nope"})
    assert r.status_code == 401


@respx.mock
async def test_maintainers_sync_endpoint(client, reseed):
    respx.get(url__regex=r"https://registry\.npmjs\.org/.*").respond(200, json=NPM_DOC)
    respx.get(url__regex=r"https://pypi\.org/pypi/.*/json").respond(200, json=PYPI_DOC)
    owners = respx.get(url__regex=r"https://crates\.io/api/v1/crates/[^/]+/owners").respond(
        200, json=OWNERS_DOC
    )
    respx.get(url__regex=r"https://crates\.io/api/v1/crates/[^/]+$").respond(200, json=CRATE_DOC)

    r = await client.post("/internal/maintainers-sync", headers=CRON)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error"] is None
    data = body["data"]
    async with SessionLocal() as s:
        n = (
            await s.execute(
                text("SELECT count(*) FROM package WHERE ecosystem IN ('npm','pypi','cargo')")
            )
        ).scalar_one()
        azer = (
            await s.execute(
                text(
                    "SELECT count(*) FROM maintained_by JOIN maintainer USING (maintainer_id) "
                    "WHERE platform = 'npm' AND username = 'azer'"
                )
            )
        ).scalar_one()
        go_links = (
            await s.execute(
                text(
                    "SELECT count(*) FROM maintained_by JOIN package USING (package_id) "
                    "JOIN maintainer USING (maintainer_id) "
                    "WHERE ecosystem = 'go' AND platform <> 'github'"
                )
            )
        ).scalar_one()
    assert data["packages_scanned"] == n == data["packages_with_data"] and n > 0
    # azer, Alice, Bob, dtolnay, serde team
    assert data["maintainers_upserted"] == 5
    assert azer > 0 and go_links == 0
    assert owners.called
