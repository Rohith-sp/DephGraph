"""Realistic usage: real-world manifests, multi-service/concurrent ingest, hostile input, paging."""

import asyncio
import json

import httpx
import pytest
import respx

from app.database import get_mongo
from app.services.manifest_parser import parse_manifest
from tests.test_ingest import body, scalar
from tests.test_runtime import payload, target, view_prunable
from tests.upstream import mock_upstreams

REQUIREMENTS = r"""
# pip-compile --generate-hashes
--index-url https://pypi.example/simple
-r base.txt
-e .
flask[async]==2.3.2 ; python_version >= "3.8" \
    --hash=sha256:aaaa \
    --hash=sha256:bbbb
Requests>=2.0,<3   # loose
numpy~=1.24.0
Django==4.2.*
pywin32==306 ; sys_platform == "win32"
Pillow===9.0
typing_extensions==4.7.1
git+https://github.com/x/y.git#egg=y
unpinned
"""

PACKAGE_JSON = json.dumps(
    {
        "name": "mono",
        "workspaces": ["packages/*"],
        "dependencies": {
            "react": "^18.2.0",
            "lodash": "~4.17",
            "left-pad": "1.x",
            "express": ">=4.0.0 <5",
            "@scope/ui": "v2.1.0",
            "local-pkg": "workspace:*",
            "aliased": "npm:other@1.0.0",
            "gh": "github:user/repo",
            "anything": "*",
            "tagged": "latest",
        },
        "devDependencies": {"jest": "^29.0.0"},
    }
)

CARGO = """
[workspace]
members = ["a", "b"]
[workspace.dependencies]
serde = "1.0"
[dependencies]
serde = { workspace = true }
tokio = { version = "1.28", features = ["full"] }
rand_core = { package = "rand", version = "0.8" }
local = { path = "../local" }
gitdep = { git = "https://example.com/x.git" }
regex = "1"
[dev-dependencies]
criterion = "0.5"
"""

GO_MOD = """module example.com/app

go 1.21

require github.com/gin-gonic/gin v1.9.0

require (
\tgithub.com/bytedance/sonic v1.8.0 // indirect
\tgolang.org/x/net v0.10.0
\tgithub.com/old/pkg v2.0.0+incompatible
)

replace example.com/old => ../old
replace (
\tgolang.org/x/net => golang.org/x/net v0.11.0
)
exclude github.com/bad/pkg v1.0.0
retract v1.0.1
"""


def pairs(eco: str, content: str) -> dict[str, tuple[str, bool]]:
    return {p.name: (p.version, p.is_direct) for p in parse_manifest(eco, content)}


def test_requirements_with_markers_extras_hashes_and_noise():
    assert pairs("pypi", REQUIREMENTS) == {
        "flask": ("2.3.2", True),
        "requests": ("2.0", True),
        "numpy": ("1.24.0", True),
        "django": ("4.2", True),
        "pywin32": ("306", True),
        "pillow": ("9.0", True),
        "typing-extensions": ("4.7.1", True),
    }


def test_package_json_workspaces_and_ranges():
    assert {n: v for n, (v, _) in pairs("npm", PACKAGE_JSON).items()} == {
        "react": "18.2.0",
        "lodash": "4.17",
        "left-pad": "1.0",
        "express": "4.0.0",
        "@scope/ui": "2.1.0",
        "jest": "29.0.0",
    }


def test_cargo_workspace_renames_and_non_registry_deps():
    assert {n: v for n, (v, _) in pairs("cargo", CARGO).items()} == {
        "tokio": "1.28",
        "rand": "0.8",
        "regex": "1",
        "criterion": "0.5",
    }


def test_go_mod_replace_indirect_exclude_retract():
    assert pairs("go", GO_MOD) == {
        "github.com/gin-gonic/gin": ("v1.9.0", True),
        "github.com/bytedance/sonic": ("v1.8.0", False),
        "golang.org/x/net": ("v0.10.0", True),
        "github.com/old/pkg": ("v2.0.0+incompatible", True),
    }


def quiet_upstreams() -> respx.MockRouter:
    """No advisories anywhere; deps.dev knows nothing."""
    router = respx.mock(assert_all_called=False)
    router.post("https://api.osv.dev/v1/querybatch").mock(
        side_effect=lambda req: httpx.Response(
            200, json={"results": [{}] * len(json.loads(req.content)["queries"])}
        )
    )
    router.get(url__regex=r"https://api\.deps\.dev/v3/.*").respond(404)
    return router


def post(client, auth, service_id, eco, content, name="m"):
    return client.post(
        "/ingest/manifest",
        json={"service_id": service_id, "ecosystem": eco, "content": content, "filename": name},
        headers=auth,
    )


async def test_four_ecosystems_across_four_services(client, auth, reseed):
    plan = [
        (1, "pypi", REQUIREMENTS, 7),
        (2, "npm", PACKAGE_JSON, 6),
        (3, "cargo", CARGO, 4),
        (4, "go", GO_MOD, 4),
    ]
    with quiet_upstreams():
        for sid, eco, content, n in plan:
            r = await post(client, auth, sid, eco, content)
            assert r.status_code == 200, r.text
            assert r.json()["data"]["packages_parsed"] == n
    for sid, _, _, n in plan:
        seq = await scalar("SELECT max(deploy_seq) FROM deployment WHERE service_id=:s", s=sid)
        direct = await scalar(
            "SELECT count(*) FROM resolves_to WHERE service_id=:s AND deploy_seq=:q AND is_direct",
            s=sid,
            q=seq,
        )
        assert direct == (3 if sid == 4 else n)  # go: one indirect


async def test_reingest_is_idempotent_for_packages_and_dedups_raw(client, auth, reseed):
    with quiet_upstreams():
        first = (await post(client, auth, 1, "pypi", REQUIREMENTS)).json()["data"]
        second = (await post(client, auth, 1, "pypi", REQUIREMENTS)).json()["data"]
    assert first["packages_inserted"] >= 1 and second["packages_inserted"] == 0
    assert second["deploy_seq"] == first["deploy_seq"] + 1
    docs = await get_mongo()["raw_manifests"].find({}).to_list(None)
    assert len(docs) == 1 and docs[0]["deploy_seqs"] == [first["deploy_seq"], second["deploy_seq"]]
    sets = [
        await scalar(
            "SELECT array_agg(package_id || '@' || version_string ORDER BY package_id) "
            "FROM resolves_to WHERE service_id=1 AND deploy_seq=:q AND is_direct",
            q=d["deploy_seq"],
        )
        for d in (first, second)
    ]
    assert sets[0] == sets[1]


async def test_concurrent_ingests_with_overlapping_packages(client, auth, reseed):
    lines = [f"shared-{i:03d}==1.{i}" for i in range(150)]
    contents = [
        "\n".join(lines),
        "\n".join(reversed(lines)),  # opposite lock order
        "\n".join(lines[50:] + lines[:50] + ["only-here==1.0"]),
    ]
    before = {
        s: await scalar("SELECT max(deploy_seq) FROM deployment WHERE service_id=:s", s=s)
        for s in (1, 2, 3)
    }
    with quiet_upstreams():
        rs = await asyncio.gather(
            *(post(client, auth, s, "pypi", c) for s, c in zip((1, 2, 3), contents, strict=True))
        )
    assert [r.status_code for r in rs] == [200, 200, 200], [r.text for r in rs]
    for (s, b), r in zip(before.items(), rs, strict=True):
        assert r.json()["data"]["deploy_seq"] == b + 1
    assert await scalar("SELECT count(*) FROM package WHERE name LIKE 'shared-%'") == 150


async def test_concurrent_ingests_same_service_get_distinct_deploy_seqs(client, auth, reseed):
    with quiet_upstreams():
        rs = await asyncio.gather(
            *(post(client, auth, 1, "pypi", f"pkg-a=={i}.0\npkg-b==1.0") for i in range(4))
        )
    assert all(r.status_code == 200 for r in rs), [r.text for r in rs]
    assert len({r.json()["data"]["deploy_seq"] for r in rs}) == 4


HOSTILE = [
    ("npm", "[]"),
    ("npm", "null"),
    ("npm", '"str"'),
    ("npm", "42"),
    ("npm", '{"dependencies": []}'),
    ("npm", '{"dependencies": "x"}'),
    ("npm", "{bad"),
    ("npm", '{"dependencies": {"a\\u0000b": "1.0.0"}}'),
    ("npm", '{"dependencies": {"a": {"nested": 1}, "b": null}}'),
    ("cargo", "[["),
    ("cargo", "dependencies = 5"),
    ("cargo", "[dependencies]\na = 5"),
    ("cargo", "[dependencies]\na = [1]"),
    ("pypi", "\x00\x00\x00"),
    ("pypi", "-r other.txt\n--hash=x"),
    ("pypi", "===\n==1.0\nfoo=="),
    ("go", "module x"),
    ("go", "require (\n"),
    ("go", "require a\nrequire a b c d"),
    ("pypi", "name==" + "9" * 101),
]


@pytest.mark.parametrize(("eco", "content"), HOSTILE)
async def test_malformed_or_hostile_manifest_is_422_never_500(client, auth, reseed, eco, content):
    with quiet_upstreams():
        r = await post(client, auth, 1, eco, content)
    assert r.status_code == 422, r.text
    assert r.json()["data"] is None and r.json()["error"]


async def test_deeply_nested_json_is_not_a_500(client, auth, reseed):
    with quiet_upstreams():
        r = await post(client, auth, 1, "npm", "[" * 100_000)
    assert r.status_code == 422


async def vulns(client, auth, **params):
    r = await client.get("/vulnerabilities", params=params, headers=auth)
    return r


async def test_vulnerabilities_paging_covers_every_row_once(client, auth):
    full = (await vulns(client, auth, page_size=200)).json()["data"]
    total = full["total"]
    assert total == len(full["items"]) >= 10
    seen = []
    for page in range(1, 5):
        d = (await vulns(client, auth, page=page, page_size=4)).json()["data"]
        assert d["total"] == total and d["page"] == page and len(d["items"]) <= 4
        seen += [v["vuln_id"] for v in d["items"]]
    assert seen == [v["vuln_id"] for v in full["items"]]  # stable order, no gaps/dupes
    beyond = (await vulns(client, auth, page=999)).json()["data"]
    assert beyond["items"] == [] and beyond["total"] == total


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page": -1},
        {"page_size": 0},
        {"page_size": 201},
        {"sort": "severity"},
        {"order": "sideways"},
        {"severity": "SEVERE"},
        {"ecosystem": "rubygems"},
        {"q": "x" * 201},
        {"page": "one"},
    ],
)
async def test_vulnerabilities_bad_params_are_422(client, auth, params):
    assert (await vulns(client, auth, **params)).status_code == 422


async def test_vulnerabilities_sort_filter_and_literal_search(client, auth):
    items = lambda d: d.json()["data"]["items"]  # noqa: E731
    desc = [v["cvss_score"] for v in items(await vulns(client, auth, page_size=200))]
    asc = [v["cvss_score"] for v in items(await vulns(client, auth, page_size=200, order="asc"))]
    nn = lambda xs: [float(x) for x in xs if x is not None]  # noqa: E731
    assert nn(desc) == sorted(nn(desc), reverse=True) and nn(asc) == sorted(nn(asc))
    assert desc[len(nn(desc)) :] == [None] * (len(desc) - len(nn(desc)))  # NULLS LAST
    assert asc[len(nn(asc)) :] == [None] * (len(asc) - len(nn(asc)))
    dated = items(await vulns(client, auth, page_size=200, sort="published_at"))
    stamps = [v["published_at"] for v in dated if v["published_at"]]
    assert stamps == sorted(stamps, reverse=True)

    for sev in ("CRITICAL", "HIGH"):
        assert {v["severity"] for v in items(await vulns(client, auth, severity=sev))} <= {sev}
    pypi = (await vulns(client, auth, ecosystem="pypi")).json()["data"]["total"]
    assert 0 < pypi < (await vulns(client, auth)).json()["data"]["total"]

    osv = items(await vulns(client, auth))[0]["osv_id"]
    hit = items(await vulns(client, auth, q=osv.lower()))
    assert osv in {v["osv_id"] for v in hit}  # case-insensitive
    for literal in ("%", "_", "\\", "'; DROP TABLE vulnerability;--"):
        r = await vulns(client, auth, q=literal)
        assert r.status_code == 200 and r.json()["data"]["total"] == 0


async def test_runtime_batch_replay_is_idempotent_and_never_regresses_liveness(
    client, auth, reseed
):
    dead = await target()
    sample = {"name": dead["name"], "version": dead["version"], "exec_count": 5}
    runs = [await client.post("/runtime/samples", json=payload([sample]), headers=auth)]
    runs.append(await client.post("/runtime/samples", json=payload([sample]), headers=auth))
    assert [r.status_code for r in runs] == [200, 200]
    assert runs[0].json()["data"]["scan_id"] != runs[1].json()["data"]["scan_id"]
    assert dead not in await view_prunable()
    # a later complete window where it never ran does not undo earlier liveness
    quiet = payload([sample | {"exec_count": 0}], complete=True)
    assert (await client.post("/runtime/samples", json=quiet, headers=auth)).status_code == 200
    assert dead not in await view_prunable()
    assert await get_mongo()["runtime_samples"].count_documents({"status": "projected"}) >= 3


async def test_full_flow_ingest_then_rescan_then_runtime(client, auth, reseed):
    """What a user does: upload, rescan after OSV learns a new advisory, report runtime use."""
    with mock_upstreams():
        up = (await client.post("/ingest/manifest", json=body(), headers=auth)).json()["data"]
    flask = {"name": "Flask", "version": "2.0.1", "exec_count": 3}
    r = await client.post(
        "/runtime/samples", json=payload([flask], deploy_seq=up["deploy_seq"]), headers=auth
    )
    assert r.status_code == 200 and r.json()["data"]["live_packages"] == 1
    with mock_upstreams():
        rescan = await client.post("/ingest/scan/1", headers=auth)
    assert rescan.json()["data"]["vulns_fetched"] == 1
