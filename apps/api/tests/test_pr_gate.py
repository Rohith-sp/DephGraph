import asyncio
import base64
import hashlib
import hmac
import json
from decimal import Decimal as D

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal, get_mongo, get_redis
from app.routers.pr_gate import normalize_repo_url
from app.services import cache, github, pr_gate_engine
from app.services.pr_gate_engine import stage2

SECRET = "hook-secret"
FILES = "https://api.github.com/repos/acme/api-gateway/pulls/7/files"
GROQ = "https://api.groq.com/openai/v1/chat/completions"


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "groq_api_key", "")
    monkeypatch.setattr(settings, "github_app_id", "")
    monkeypatch.setattr(settings, "github_private_key_base64", "")


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def payload(action="opened", repo="https://github.com/acme/api-gateway") -> dict:
    return {
        "action": action,
        "number": 7,
        "pull_request": {
            "number": 7,
            "html_url": repo + "/pull/7",
            "user": {"login": "alice-dev"},
            "head": {"sha": "abc123"},
        },
        "repository": {"html_url": repo, "full_name": "acme/api-gateway"},
        "installation": {"id": 99},
    }


async def post(client, body: dict, event="pull_request", sig=None, headers_extra=None):
    raw = json.dumps(body).encode()
    return await client.post(
        "/pr-gate/webhook",
        content=raw,
        headers={
            "X-GitHub-Event": event,
            "X-Hub-Signature-256": sig or sign(raw),
            "Content-Type": "application/json",
            **(headers_extra or {}),
        },
    )


async def mkpkg(name, cvss=None, fixed="1.1.0", vuln=True, copyleft=False, age=0, yanked=False):
    """Insert fresh pypi package `name` 1.0.0 with an optional vuln.

    Abandonment = age/1yr*15 (+40 if yanked); age=0 -> 0.
    """
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
                "is_yanked) VALUES (:p, '1.0.0', :l, now() - make_interval(secs => :age), :y)"
            ),
            {"p": pid, "l": 3 if copyleft else 1, "age": age, "y": yanked},
        )
        if vuln:
            vid = (
                await s.execute(
                    text(
                        "INSERT INTO vulnerability (osv_id, severity, cvss_score) "
                        "VALUES (:o, 'HIGH', :c) RETURNING vuln_id"
                    ),
                    {"o": f"T-{name}", "c": cvss},
                )
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO affects (package_id, version_string, vuln_id, fixed_in) "
                    "VALUES (:p, '1.0.0', :v, :f)"
                ),
                {"p": pid, "v": vid, "f": fixed},
            )


async def review_rows(client, auth, service_id=1) -> list[dict]:
    got = (await client.get(f"/pr-gate/verdicts/{service_id}", headers=auth)).json()["data"]
    return [x for x in got if x["pr_number"] == 7]


async def review(client, auth, body=None, **kw) -> dict:
    """POST the webhook, assert the 200 verdict, then read PR 7's stored verdicts."""
    r = await post(client, body or payload(), **kw)
    assert r.status_code == 200 and r.json()["data"]["pr_number"] == 7, r.text
    stored = await review_rows(client, auth)
    return {
        "verdict": max((x["verdict"] for x in stored), key=pr_gate_engine.RANK.get, default="PASS"),
        "stage_reached": max((x["stage_reached"] for x in stored), default=1),
        "deps": [
            {
                "name": x["dep_name"],
                "stage_reached": x["stage_reached"],
                "reasoning": x["reasoning"],
            }
            for x in stored
        ],
    }


def files(*names: str):
    patch = "@@ -1 +1,2 @@\n flask==2.0.0\n" + "".join(f"+{n}==1.0.0\n" for n in names)
    return respx.get(FILES).respond(200, json=[{"filename": "requirements.txt", "patch": patch}])


def groq_reply(verdict: str, reasoning: str) -> dict:
    content = json.dumps({"verdict": verdict, "reasoning": reasoning})
    return {
        "id": "1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
    }


@pytest.mark.parametrize(
    "cvss,fixed,copyleft,aband,want",
    [
        ([], [], False, 0, "PASS"),
        ([D("5.9")], [True], False, 39, "PASS"),
        ([D("6.0")], [True], False, 0, "ADVISORY"),
        ([D("8.9")], [True], False, 0, "ADVISORY"),
        ([D("9.0")], [True], False, 0, "BLOCK"),
        ([], [], False, 40, "ADVISORY"),
        ([], [], False, 79, "ADVISORY"),
        ([], [], False, 80, "BLOCK"),
        ([], [], True, 0, "BLOCK"),
        ([D("3.0")], [False], False, 0, "ADVISORY"),  # no fixed_in
        ([None], [True], False, 0, "ADVISORY"),  # NULL cvss
        ([D("9.8"), D("6.5")], [True, True], False, 0, "BLOCK"),
        ([], [], False, None, "PASS"),  # unknown abandonment
    ],
)
def test_stage2_rules(cvss, fixed, copyleft, aband, want):
    assert stage2(cvss, fixed, copyleft, aband)[0] == want


async def test_signature_rejected(client):
    assert (await post(client, payload(), sig="sha256=" + "0" * 64)).status_code == 401
    raw = json.dumps(payload()).encode()
    r = await client.post(
        "/pr-gate/webhook", content=raw, headers={"X-GitHub-Event": "pull_request"}
    )
    assert r.status_code == 401 and r.json()["error"]


async def test_non_pr_event_and_action_skipped(client):
    r = await post(client, {"zen": "x"}, event="ping")
    assert r.status_code == 200 and r.json()["data"] == {"skipped": True}
    assert (await post(client, payload("closed"))).json()["data"] == {"skipped": True}


@respx.mock
async def test_unknown_repo_is_acked_and_never_fetched(client, auth):
    r = await post(client, payload(repo="https://github.com/other/thing"))
    assert r.status_code == 200 and r.json()["data"] == {"skipped": True}
    assert not respx.calls  # no registered service: GitHub is never called
    assert await review_rows(client, auth) == []


async def test_malformed_payload_422(client):
    assert (await post(client, {"action": "opened"})).status_code == 422


@respx.mock
async def test_spec_phase6_block_and_persist(client, auth, reseed):
    """PRD Phase 6 snippet: the webhook answers 200 with the verdict itself."""
    await mkpkg("evilcrit", cvss=9.8)
    files("evilcrit")
    r = await post(client, payload(), headers_extra={"X-GitHub-Delivery": "d-1"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["verdict"] in ["PASS", "ADVISORY", "BLOCK"] and data["verdict"] == "BLOCK"
    assert data["stage_reached"] in [1, 2, 3]
    assert 0 <= data["processing_ms"] < 30_000
    assert await review_rows(client, auth)  # and the verdict is persisted
    assert [d["name"] for d in data["deps"]] == ["evilcrit"]
    assert data["deps"][0]["stage_reached"] == 2 and "CVSS" in data["deps"][0]["reasoning"]


@respx.mock
async def test_stage1_pass_and_worst_verdict(client, auth, reseed):
    await mkpkg("lowrisk", cvss=3.0)
    await mkpkg("copyleftpkg", vuln=False, copyleft=True)
    files("lowrisk", "unknownpkg")
    d = await review(client, auth)
    assert d["verdict"] == "PASS"
    by = {x["name"]: x for x in d["deps"]}
    assert by["unknownpkg"]["stage_reached"] == 1 and by["lowrisk"]["stage_reached"] == 2

    respx.clear()
    files("unknownpkg", "copyleftpkg")
    d = await review(client, auth, payload("synchronize"))
    assert d["verdict"] == "BLOCK"  # copyleft reachable without any vuln


@respx.mock
async def test_stage3_groq_refines_advisory(client, auth, monkeypatch, reseed):
    monkeypatch.setattr(settings, "groq_api_key", "gsk_test")
    await mkpkg("midrisk", cvss=7.0)
    files("midrisk")
    reply = groq_reply("BLOCK", "Exploitable in auth path.")
    route = respx.post(GROQ).respond(200, json=reply)
    d = await review(client, auth)
    assert d["verdict"] == "BLOCK" and d["stage_reached"] == 3
    assert d["deps"][0]["reasoning"].endswith("| LLM: Exploitable in auth path.")
    assert d["deps"][0]["reasoning"].startswith("CVSS 7.00 in 6.0-8.9")  # rule reason kept
    prompt = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert len(prompt) <= 1200
    stored = await get_mongo()["review_responses"].find_one({"dep_name": "midrisk"})
    assert stored and "BLOCK" in stored["response"]


@respx.mock
@pytest.mark.parametrize("key,status", [("", 500), ("gsk", 500), ("gsk", 429)])
async def test_groq_failure_keeps_stage2(client, auth, monkeypatch, reseed, key, status):
    monkeypatch.setattr(settings, "groq_api_key", key)
    await mkpkg("midrisk2", cvss=7.0)
    files("midrisk2")
    respx.post(GROQ).respond(status)
    d = await review(client, auth)
    assert d["verdict"] == "ADVISORY" and d["stage_reached"] == 2


@respx.mock
async def test_groq_429_then_success(client, auth, monkeypatch, reseed):
    monkeypatch.setattr(settings, "groq_api_key", "gsk")
    await mkpkg("midrisk3", cvss=7.0)
    files("midrisk3")
    respx.post(GROQ).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(200, json=groq_reply("ADVISORY", "ok")),
        ]
    )
    d = await review(client, auth)
    assert d["stage_reached"] == 3 and d["verdict"] == "ADVISORY"


@respx.mock
async def test_check_run_posted_when_configured(client, auth, monkeypatch, reseed):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    monkeypatch.setattr(settings, "github_app_id", "123")
    monkeypatch.setattr(settings, "github_private_key_base64", base64.b64encode(pem).decode())
    await mkpkg("evilcrit2", cvss=9.9)
    respx.post("https://api.github.com/app/installations/99/access_tokens").respond(
        201, json={"token": "ghs_x"}
    )
    files("evilcrit2")
    check = respx.post("https://api.github.com/repos/acme/api-gateway/check-runs").respond(
        201, json={}
    )
    assert (await review(client, auth))["verdict"] == "BLOCK"
    sent = json.loads(check.calls[0].request.content)
    assert sent["conclusion"] == "failure" and sent["head_sha"] == "abc123"
    assert check.calls[0].request.headers["authorization"] == "Bearer ghs_x"


async def test_verdicts_endpoint(client, auth):
    r = await client.get("/pr-gate/verdicts/1", headers=auth)
    assert r.status_code == 200
    assert {
        "pr_number",
        "dep_name",
        "verdict",
        "stage_reached",
        "reasoning",
        "decided_at",
    } <= set(r.json()["data"][0])
    assert (await client.get("/pr-gate/verdicts/9999", headers=auth)).status_code == 404
    assert (await client.get("/pr-gate/verdicts/abc", headers=auth)).status_code == 422
    assert (await client.get("/pr-gate/verdicts/1")).status_code == 401


def test_added_deps_parsers():
    assert github.added_deps("req/requirements.txt", "+++ b\n+flask==2.0.1\n-old==1\n") == [
        ("pypi", "flask", "2.0.1")
    ]
    assert github.added_deps("package.json", '+    "axios": "^1.6.0",\n+  "x": 1') == [
        ("npm", "axios", "1.6.0")
    ]
    assert github.added_deps("Cargo.toml", '+tokio = "1.2"') == [("cargo", "tokio", "1.2")]
    assert github.added_deps("go.mod", "+\tgithub.com/gin-gonic/gin v1.9.0") == [
        ("go", "github.com/gin-gonic/gin", "v1.9.0")
    ]
    assert github.added_deps("README.md", "+hello") == []


def test_prompt_truncated():
    dep = pr_gate_engine.DepVerdict("x" * 5000, "1", "ADVISORY", 2, "r")
    assert len(pr_gate_engine.build_prompt(dep)) <= 1200


YEAR = 31557600


@pytest.mark.parametrize(
    "age,yanked,want",
    [
        (int(2.6 * YEAR), False, (1, "PASS")),  # 39
        (0, True, (2, "ADVISORY")),  # 40
        (int(2.6 * YEAR), True, (2, "ADVISORY")),  # 79
        (YEAR * 8 // 3, True, (2, "BLOCK")),  # 80
    ],
)
async def test_stage1_abandonment_boundaries(age, yanked, want, reseed):
    name = f"aband{age}{yanked}"
    await mkpkg(name, vuln=False, age=age, yanked=yanked)
    async with SessionLocal() as s, s.begin():
        d = await pr_gate_engine.evaluate(s, "pypi", name, "1.0.0")
    assert (d.stage_reached, d.verdict) == want


async def _verdicts(client, auth):
    rows = (await client.get("/pr-gate/verdicts/1", headers=auth)).json()["data"]
    return {r["dep_name"]: r["verdict"] for r in rows if r["pr_number"] == 7}


@respx.mock
async def test_same_name_deps_keep_worst_and_redelivery_replaces(client, auth, reseed):
    await mkpkg("dupe", cvss=9.8)  # 1.0.0 BLOCK; 2.0.0 is unknown -> PASS

    def hunk(ver):
        return "@@ -1 +1,2 @@\n flask==2.0.0\n+dupe==" + ver + "\n"

    respx.get(FILES).respond(  # same name in two files -> two deps, one PK
        200,
        json=[
            {"filename": "requirements.txt", "patch": hunk("2.0.0")},
            {"filename": "dev/requirements.txt", "patch": hunk("1.0.0")},
        ],
    )
    d = await review(client, auth)
    assert d["verdict"] == "BLOCK" and len(d["deps"]) == 1  # one PK row: the worst
    assert await _verdicts(client, auth) == {"dupe": "BLOCK"}  # PASS did not hide BLOCK

    respx.clear()  # synchronize: only the safe version remains -> stale BLOCK is gone
    patch = "@@ -1 +1,2 @@\n flask==2.0.0\n+dupe==2.0.0\n"
    respx.get(FILES).respond(200, json=[{"filename": "requirements.txt", "patch": patch}])
    await review(client, auth, payload("synchronize"))
    assert await _verdicts(client, auth) == {"dupe": "PASS"}


def test_prompt_keeps_instruction_with_many_vulns():
    vulns = [
        {"osv_id": f"GHSA-{i:04d}-xxxx-yyyy", "cvss_score": 7.5, "fixed_in": "9.9.9"}
        for i in range(30)
    ]
    dep = pr_gate_engine.DepVerdict(
        "x" * 5000, "1" * 500, "ADVISORY", 2, "r" * 5000, facts={"vulns": vulns}
    )
    prompt = pr_gate_engine.build_prompt(dep)
    assert len(prompt) <= 1200
    assert prompt.endswith('"reasoning":"<=2 sentences"}')
    assert "Reply JSON only" in prompt


@respx.mock
async def test_stage3_concurrent_capped_and_client_closed(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "gsk")
    live = peak = 0

    async def slow(request):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.05)
        live -= 1
        return httpx.Response(200, json=groq_reply("ADVISORY", "ok"))

    respx.post(GROQ).mock(side_effect=slow)
    deps = [pr_gate_engine.DepVerdict(f"d{i}", "1", "ADVISORY", 2, "r") for i in range(7)]
    overall, stage = await pr_gate_engine.refine(deps, 1, 7)
    assert (overall, stage) == ("ADVISORY", 3)
    assert 1 < peak <= 3
    assert pr_gate_engine._client().timeout == pr_gate_engine.GROQ_TIMEOUT
    await pr_gate_engine.close_groq()
    assert pr_gate_engine._groq is None


@respx.mock
async def test_stage3_budget_keeps_stage2(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "gsk")
    monkeypatch.setattr(pr_gate_engine, "STAGE3_BUDGET", 0.05)

    async def hang(request):
        await asyncio.sleep(5)

    respx.post(GROQ).mock(side_effect=hang)
    dep = pr_gate_engine.DepVerdict("slow", "1", "ADVISORY", 2, "r")
    assert await pr_gate_engine.refine([dep], 1, 7) == ("ADVISORY", 2)


# --- semantic redundancy (third PR-gate signal) ---------------------------------------------


async def mkcluster_pkgs(layout: dict[str, list[str]], deployed: list[str], older=()):
    """layout = {cluster label: [new pypi package names]} (all at 1.0.0, MIT, fresh, no vulns).

    Adds deployment 3 to service 1 (the new latest) resolving `deployed`; `older` packages are
    resolved only by the older deployment 2.
    """
    async with SessionLocal() as s, s.begin():
        for label, names in layout.items():
            cid = (
                await s.execute(
                    text(
                        "INSERT INTO semantic_cluster (label, member_count) VALUES (:l, :n) "
                        "RETURNING cluster_id"
                    ),
                    {"l": label, "n": len(names)},
                )
            ).scalar_one()
            for n in names:
                pid = (
                    await s.execute(
                        text(
                            "INSERT INTO package (ecosystem, name) VALUES ('pypi', :n) "
                            "RETURNING package_id"
                        ),
                        {"n": n},
                    )
                ).scalar_one()
                await s.execute(
                    text(
                        "INSERT INTO package_version (package_id, version_string, license_id, "
                        "cluster_id, published_at) VALUES (:p, '1.0.0', 1, :c, now())"
                    ),
                    {"p": pid, "c": cid},
                )
        await s.execute(text("INSERT INTO deployment (service_id, deploy_seq) VALUES (1, 3)"))
        for seq, names in ((3, deployed), (2, older)):
            for n in names:
                await s.execute(
                    text(
                        "INSERT INTO resolves_to (service_id, deploy_seq, package_id, "
                        "version_string, depth, is_direct) SELECT 1, :q, package_id, '1.0.0', "
                        "0, TRUE FROM package WHERE ecosystem = 'pypi' AND name = :n"
                    ),
                    {"q": seq, "n": n},
                )


REDUNDANT = [{"package": "requests", "cluster_id": 4, "cluster_label": "web / requests / client"}]


@pytest.mark.parametrize(
    "cvss,copyleft,aband,redundancy,want",
    [
        ([], False, 0, REDUNDANT, "ADVISORY"),  # redundancy alone
        ([], False, 0, [], "PASS"),
        ([], False, 0, None, "PASS"),
        ([D("9.8")], False, 0, REDUNDANT, "BLOCK"),  # BLOCK still wins
        ([], True, 0, REDUNDANT, "BLOCK"),
        ([D("3.0")], False, 10, REDUNDANT, "ADVISORY"),
    ],
)
def test_stage2_redundancy_rules(cvss, copyleft, aband, redundancy, want):
    verdict, why = stage2(cvss, [True] * len(cvss), copyleft, aband, redundancy)
    assert verdict == want
    if redundancy and want == "ADVISORY":
        assert "same job as requests (cluster 'web / requests / client')" in why


def test_redundancy_reasons_grouped_and_capped():
    red = [{"package": n, "cluster_label": "web"} for n in ["a", "b", "c", "d", "e", "a"]]
    red.append({"package": "x", "cluster_label": "util"})
    assert pr_gate_engine.redundancy_reasons(red) == [
        "same job as a, b, c +2 more (cluster 'web')",
        "same job as x (cluster 'util')",
    ]
    assert pr_gate_engine.redundancy_reasons([]) == []


async def test_redundancy_advisory_survives_stage1(reseed):
    await mkcluster_pkgs({"web / http / client": ["rhttp", "rreq"]}, deployed=["rreq"])
    async with SessionLocal() as s, s.begin():
        d = await pr_gate_engine.evaluate(s, "pypi", "rhttp", "1.0.0", service_id=1)
        # a version not in the DB still gets the package's cluster
        d2 = await pr_gate_engine.evaluate(s, "pypi", "rhttp", "9.9.9", service_id=1)
        # without a service there is nothing to be redundant with -> stage 1 PASS
        d3 = await pr_gate_engine.evaluate(s, "pypi", "rhttp", "1.0.0")
    assert (d.verdict, d.stage_reached) == ("ADVISORY", 2)
    assert d.reasoning == "same job as rreq (cluster 'web / http / client')"
    assert d.facts["redundancy"][0]["package"] == "rreq"
    assert (d2.verdict, d2.stage_reached) == ("ADVISORY", 2)
    assert (d3.verdict, d3.stage_reached) == ("PASS", 1)


async def test_redundancy_no_signal_cases(reseed):
    await mkcluster_pkgs(
        {"web / http / client": ["nhttp", "nold"], "date / time": ["ndate"]},
        deployed=["ndate", "nhttp"],
        older=["nold"],
    )
    async with SessionLocal() as s, s.begin():
        # ndate's cluster has no other package in the latest deployment (different cluster)
        other = await pr_gate_engine.evaluate(s, "pypi", "ndate", "1.0.0", service_id=1)
        # nhttp's only cluster-mate (nold) is in the OLDER deployment, and nhttp itself is
        # already deployed (a package is not redundant with itself)
        same = await pr_gate_engine.evaluate(s, "pypi", "nhttp", "1.0.0", service_id=1)
        unknown = await pr_gate_engine.evaluate(s, "pypi", "nosuchpkg", "1.0.0", service_id=1)
        await s.execute(text("INSERT INTO package (ecosystem, name) VALUES ('pypi', 'nocl')"))
        await s.execute(
            text(
                "INSERT INTO package_version (package_id, version_string, license_id, "
                "published_at) SELECT package_id, '1.0.0', 1, now() FROM package "
                "WHERE ecosystem = 'pypi' AND name = 'nocl'"
            )
        )
        unclustered = await pr_gate_engine.evaluate(s, "pypi", "nocl", "1.0.0", service_id=1)
        # npm package in nhttp's cluster: deployed cluster-mate is pypi -> other ecosystem
        await s.execute(text("INSERT INTO package (ecosystem, name) VALUES ('npm', 'njs')"))
        await s.execute(
            text(
                "INSERT INTO package_version (package_id, version_string, license_id, "
                "cluster_id, published_at) SELECT p.package_id, '1.0.0', 1, "
                "(SELECT cluster_id FROM semantic_cluster WHERE label = 'web / http / client' "
                "ORDER BY cluster_id DESC LIMIT 1), now() FROM package p "
                "WHERE ecosystem = 'npm' AND name = 'njs'"
            )
        )
        cross = await pr_gate_engine.evaluate(s, "npm", "njs", "1.0.0", service_id=1)
    for d in (other, same, unknown, unclustered, cross):
        assert (d.verdict, d.stage_reached) == ("PASS", 1), d


@respx.mock
async def test_webhook_flags_redundant_dep(client, auth, reseed):
    await mkcluster_pkgs({"web / http / client": ["whttp", "wreq"]}, deployed=["wreq"])
    files("whttp")
    d = await review(client, auth)
    assert d["verdict"] == "ADVISORY" and d["stage_reached"] == 2
    assert "same job as wreq" in d["deps"][0]["reasoning"]
    assert await _verdicts(client, auth) == {"whttp": "ADVISORY"}


def test_prompt_with_redundancy_within_budget():
    vulns = [
        {"osv_id": f"GHSA-{i:04d}-xxxx-yyyy", "cvss_score": 7.5, "fixed_in": "9.9.9"}
        for i in range(30)
    ]
    red = [{"package": "p" * 200 + str(i), "cluster_label": "l" * 120} for i in range(20)]
    dep = pr_gate_engine.DepVerdict(
        "x" * 5000, "1" * 500, "ADVISORY", 2, "r" * 5000, facts={"vulns": vulns, "redundancy": red}
    )
    prompt = pr_gate_engine.build_prompt(dep)
    assert len(prompt) <= 1200
    assert prompt.endswith('"reasoning":"<=2 sentences"}')
    assert "Redundant with packages already deployed: same job as" in prompt

    small = pr_gate_engine.DepVerdict(
        "httpx", "0.27.0", "ADVISORY", 2, "same job as requests", facts={"redundancy": REDUNDANT}
    )
    p = pr_gate_engine.build_prompt(small)
    assert "same job as requests (cluster 'web / requests / client')" in p and len(p) <= 1200
    plain = pr_gate_engine.build_prompt(pr_gate_engine.DepVerdict("a", "1", "ADVISORY", 2, "r"))
    assert "Redundant with packages already deployed: none" in plain


# --- realistic webhook flows (GitHub mocked with respx; review fixes B1-B10) -----------------


def configure_app(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    monkeypatch.setattr(settings, "github_app_id", "123")
    monkeypatch.setattr(settings, "github_private_key_base64", base64.b64encode(pem).decode())


TOKEN_URL = "https://api.github.com/app/installations/99/access_tokens"
CHECKS = "https://api.github.com/repos/acme/api-gateway/check-runs"


def req_patch(*lines: str) -> str:
    return "@@ -1 +1,9 @@\n flask==2.0.0\n" + "".join(f"+{ln}\n" for ln in lines)


def filler(n: int, start: int = 0) -> list[dict]:
    return [
        {"filename": f"src/mod{i}.py", "patch": "@@ -1 +1 @@\n+x = 1\n"}
        for i in range(start, start + n)
    ]


def paged(pages: list[list[dict]]) -> None:
    for i, page in enumerate(pages, 1):
        respx.get(FILES, params={"per_page": "100", "page": str(i)}).respond(200, json=page)


@respx.mock
async def test_pr_files_are_paginated(client, auth, reseed):  # B1: dep after file 100 is seen
    await mkpkg("latedep", cvss=9.9)
    paged(
        [
            filler(100),
            filler(100, 100),
            [{"filename": "requirements.txt", "patch": req_patch("latedep==1.0.0")}],
        ]
    )
    d = await review(client, auth)
    assert d["verdict"] == "BLOCK" and [x["name"] for x in d["deps"]] == ["latedep"]
    assert len(respx.calls) == 3


@respx.mock
async def test_missing_patch_is_advisory_not_pass(client, auth, reseed):  # B1
    respx.get(FILES).respond(
        200, json=[{"filename": "requirements.txt", "status": "modified", "changes": 9000}]
    )
    d = await review(client, auth)
    assert d["verdict"] == "ADVISORY" and d["stage_reached"] == 2
    assert (
        d["deps"][0]["name"]
        == "[not inspected] requirements.txt: diff too large for GitHub to include, not inspected"[
            :255
        ]
    )


@pytest.mark.parametrize(
    "name", ["pyproject.toml", "backend/poetry.lock", "web/package-lock.json", "Cargo.lock"]
)
@respx.mock
async def test_unparsed_manifests_are_advisory(client, auth, reseed, name):  # B1
    respx.get(FILES).respond(200, json=[{"filename": name, "patch": "@@ -1 +1 @@\n+x\n"}])
    d = await review(client, auth)
    assert d["verdict"] == "ADVISORY" and "not inspected" in d["deps"][0]["reasoning"]


@respx.mock
async def test_unchanged_rename_and_removed_manifests_are_not_flagged(client, auth, reseed):  # B1
    respx.get(FILES).respond(
        200,
        json=[
            {"filename": "requirements.txt", "status": "renamed", "changes": 0},
            {"filename": "old/requirements.txt", "status": "removed", "changes": 5},
            {"filename": "README.md"},
        ],
    )
    assert (await review(client, auth))["verdict"] == "PASS"


@respx.mock
async def test_mixed_manifests_in_one_pr(client, auth, reseed):
    """requirements.txt + package.json + a source file: both ecosystems evaluated, worst wins."""
    await mkpkg("evilmulti", cvss=9.5)
    respx.get(FILES).respond(
        200,
        json=[
            {"filename": "src/app.py", "patch": "@@ -1 +1 @@\n+import evilmulti\n"},
            {"filename": "requirements.txt", "patch": req_patch("evilmulti==1.0.0")},
            {
                "filename": "web/package.json",
                "patch": '@@ -3 +3,2 @@\n+    "lodash": "4.17.15",\n+    "private": true\n',
            },
        ],
    )
    d = await review(client, auth)
    names = {x["name"] for x in d["deps"]}
    assert {"evilmulti", "lodash"} <= names and d["verdict"] == "BLOCK"


@respx.mock
async def test_synchronize_redelivery_replaces_verdicts_and_notes(client, auth, reseed):
    await mkpkg("syncdep", cvss=9.1)
    respx.get(FILES).respond(
        200,
        json=[
            {"filename": "requirements.txt", "patch": req_patch("syncdep==1.0.0")},
            {"filename": "pyproject.toml", "patch": "@@ -1 +1 @@\n+x\n"},
        ],
    )
    first = await review(client, auth)
    assert first["verdict"] == "BLOCK" and len(first["deps"]) == 2
    respx.clear()
    respx.get(FILES).respond(
        200, json=[{"filename": "requirements.txt", "patch": req_patch("unknownthing==1.0.0")}]
    )
    second = await review(client, auth, payload("synchronize"))
    assert second["verdict"] == "PASS" and [x["name"] for x in second["deps"]] == ["unknownthing"]


@pytest.mark.parametrize(
    "stored",
    [
        "https://github.com/acme/api-gateway/",
        "https://GitHub.com/Acme/API-Gateway.git",
        "http://github.com/acme/api-gateway",
        "  https://github.com/acme/api-gateway.git/ ",
    ],
)
@respx.mock
async def test_repo_url_matches_after_normalization(client, auth, reseed, stored):  # B5
    async with SessionLocal() as s, s.begin():
        await s.execute(
            text("UPDATE service SET repo_url = :u WHERE service_id = 1"), {"u": stored}
        )
    await mkpkg("urlpkg", cvss=9.9)
    files("urlpkg")
    assert (await review(client, auth, payload(repo="https://github.com/acme/api-gateway.git/")))[
        "verdict"
    ] == "BLOCK"


@pytest.mark.parametrize(
    "raw,want",
    [
        ("HTTP://GitHub.com/Acme/X.git/", "https://github.com/acme/x"),
        ("https://github.com/acme/x//", "https://github.com/acme/x"),
        ("https://github.com/acme/x", "https://github.com/acme/x"),
        (" https://github.com/acme/xgit ", "https://github.com/acme/xgit"),
    ],
)
def test_normalize_repo_url(raw, want):
    assert normalize_repo_url(raw) == want


@respx.mock
async def test_monorepo_services_each_get_verdicts(client, auth, reseed):  # B5
    async with SessionLocal() as s, s.begin():
        sid = (
            await s.execute(
                text(
                    "INSERT INTO service (team_id, name, repo_url, language) SELECT team_id, "
                    "'api-gateway-worker', 'https://github.com/acme/api-gateway/', 'python' "
                    "FROM service WHERE service_id = 1 RETURNING service_id"
                )
            )
        ).scalar_one()
    await mkpkg("monodep", cvss=9.9)
    route = files("monodep")
    await review(client, auth)
    assert route.call_count == 1  # one PR fetch for both services
    for service_id in (1, sid):
        assert [
            (x["dep_name"], x["verdict"]) for x in await review_rows(client, auth, service_id)
        ] == [("monodep", "BLOCK")]


@respx.mock
async def test_monorepo_redundancy_is_judged_per_service(client, auth, reseed):
    await mkcluster_pkgs(
        {"web / http / client": ["mhttp", "mreq"]}, deployed=["mreq"]
    )  # service 1 only
    async with SessionLocal() as s, s.begin():
        sid = (
            await s.execute(
                text(
                    "INSERT INTO service (team_id, name, repo_url, language) SELECT team_id, 'other', "
                    "repo_url, 'python' FROM service WHERE service_id = 1 RETURNING service_id"
                )
            )
        ).scalar_one()
    files("mhttp")
    await review(client, auth)
    assert [x["verdict"] for x in await review_rows(client, auth, 1)] == ["ADVISORY"]
    assert [x["verdict"] for x in await review_rows(client, auth, sid)] == ["PASS"]


@respx.mock
async def test_failure_posts_neutral_check_run_and_returns_502(
    client, auth, monkeypatch, reseed
):  # B2
    configure_app(monkeypatch)
    respx.post(TOKEN_URL).respond(201, json={"token": "ghs_x"})
    respx.get(FILES).respond(500)
    check = respx.post(CHECKS).respond(201, json={})
    r = await post(client, payload())
    assert r.status_code == 502 and "could not evaluate" in r.json()["error"]
    sent = json.loads(check.calls[0].request.content)
    assert sent["conclusion"] == "neutral" and "could not evaluate" in sent["output"]["summary"]
    assert await review_rows(client, auth) == []


@respx.mock
async def test_token_failure_still_evaluates_without_check_run(
    client, auth, monkeypatch, reseed
):  # B8
    configure_app(monkeypatch)
    respx.post(TOKEN_URL).respond(401, json={"message": "Bad credentials"})
    check = respx.post(CHECKS).respond(201, json={})
    await mkpkg("tokenless", cvss=9.9)
    files("tokenless")
    assert (await review(client, auth))["verdict"] == "BLOCK"
    assert not check.called


@respx.mock
async def test_check_run_failure_is_not_fatal(client, auth, monkeypatch, reseed):
    configure_app(monkeypatch)
    respx.post(TOKEN_URL).respond(201, json={"token": "ghs_x"})
    respx.post(CHECKS).respond(422, json={})
    await mkpkg("chk", cvss=9.9)
    files("chk")
    assert (await review(client, auth))["verdict"] == "BLOCK"


# --- Stage 3 guard ---------------------------------------------------------------------------


def groq_raw(content: str) -> dict:
    r = groq_reply("X", "x")
    r["choices"][0]["message"]["content"] = content
    return r


@pytest.mark.parametrize(
    "content",
    [
        '{"verdict":"PASS","reasoning":"fine"}',  # the LLM can never downgrade
        '{"verdict":"pass"}',
        "not json",
        "{}",
        "",
        "[1]",
        '"BLOCK"',
        '{"verdict":["BLOCK"]}',
    ],
)
@respx.mock
async def test_stage3_guard_rejects_bad_replies(client, auth, monkeypatch, reseed, content):  # B10
    monkeypatch.setattr(settings, "groq_api_key", "gsk")
    await mkpkg("guarded", cvss=7.0)
    files("guarded")
    respx.post(GROQ).respond(200, json=groq_raw(content))
    d = await review(client, auth)
    assert d["verdict"] == "ADVISORY" and d["stage_reached"] == 2
    assert await get_redis().keys("dephgraph:prompts:*") == []  # invalid replies are not cached


@pytest.mark.parametrize("verdict", ["block", " Block\n", "BLOCK"])
@respx.mock
async def test_stage3_verdict_case_insensitive(client, auth, monkeypatch, reseed, verdict):  # B9
    monkeypatch.setattr(settings, "groq_api_key", "gsk")
    await mkpkg("casey", cvss=7.0)
    files("casey")
    respx.post(GROQ).respond(200, json=groq_raw(json.dumps({"verdict": verdict, "reasoning": "r"})))
    d = await review(client, auth)
    assert d["verdict"] == "BLOCK" and d["stage_reached"] == 3


@respx.mock
async def test_stage3_timeout_keeps_stage2(client, auth, monkeypatch, reseed):
    monkeypatch.setattr(settings, "groq_api_key", "gsk")
    await mkpkg("slowllm", cvss=7.0)
    files("slowllm")
    respx.post(GROQ).mock(side_effect=httpx.ConnectTimeout("t"))
    d = await review(client, auth)
    assert d["verdict"] == "ADVISORY" and d["stage_reached"] == 2


@respx.mock
async def test_stage3_prompt_cache_spares_groq_on_redelivery(
    client, auth, monkeypatch, reseed
):  # B7
    monkeypatch.setattr(settings, "groq_api_key", "gsk")
    await mkpkg("cachedep", cvss=7.0)
    files("cachedep")
    route = respx.post(GROQ).respond(200, json=groq_reply("BLOCK", "bad"))
    first = await review(client, auth)
    second = await review(client, auth, payload("synchronize"))
    assert route.call_count == 1
    assert first["verdict"] == second["verdict"] == "BLOCK" and second["stage_reached"] == 3
    keys = await get_redis().keys("dephgraph:prompts:*")
    assert len(keys) == 1 and 0 < await get_redis().ttl(keys[0]) <= pr_gate_engine.PROMPT_CACHE_TTL
    assert await get_mongo()["review_responses"].count_documents({"dep_name": "cachedep"}) >= 1


@respx.mock
async def test_stage3_redundancy_only_never_goes_to_groq(client, auth, monkeypatch, reseed):  # B4
    monkeypatch.setattr(settings, "groq_api_key", "gsk")
    await mkcluster_pkgs({"web / http / client": ["ghttp", "greq"]}, deployed=["greq"])
    files("ghttp")
    route = respx.post(GROQ).respond(200, json=groq_reply("BLOCK", "duplicate client"))
    d = await review(client, auth)
    assert d["verdict"] == "ADVISORY" and d["stage_reached"] == 2 and not route.called


async def test_version_bump_of_deployed_package_is_not_redundant(reseed):  # B3
    await mkcluster_pkgs(
        {"web / http / client": ["bhttp", "breq", "bnew"]}, deployed=["breq", "bhttp"]
    )
    async with SessionLocal() as s, s.begin():
        bump = await pr_gate_engine.evaluate(s, "pypi", "bhttp", "1.0.0", service_id=1)
        bump2 = await pr_gate_engine.evaluate(s, "pypi", "bhttp", "2.0.0", service_id=1)
        new = await pr_gate_engine.evaluate(s, "pypi", "bnew", "1.0.0", service_id=1)
    assert (bump.verdict, bump2.verdict) == ("PASS", "PASS")
    assert new.verdict == "ADVISORY" and {r["package"] for r in new.facts["redundancy"]} == {
        "breq",
        "bhttp",
    }


def at(updated_at: str, sha: str, action="synchronize") -> dict:
    body = payload(action)
    body["pull_request"].update(updated_at=updated_at, head={"sha": sha})
    return body


@respx.mock
async def test_older_delivery_cannot_overwrite_newer_verdicts(client, auth, reseed):  # B6
    await mkpkg("evilcrit2", cvss=9.8)
    await mkpkg("quietpkg2", cvss=3.0)
    redis = get_redis()

    files("evilcrit2")
    await review(client, auth, at("2026-11-22T10:00:02Z", "new-head"))
    assert [x["dep_name"] for x in await review_rows(client, auth)] == ["evilcrit2"]
    gen = await redis.get(cache.GEN_KEY)

    respx.clear()
    files("quietpkg2")  # the OLDER push's evaluation finishes last
    await review(client, auth, at("2026-11-22T10:00:01Z", "old-head"))
    assert [x["dep_name"] for x in await review_rows(client, auth)] == ["evilcrit2"]
    assert await redis.get(cache.GEN_KEY) == gen  # nothing stored, nothing invalidated
    async with SessionLocal() as s:
        sha = (
            await s.execute(text("SELECT head_sha FROM pr_review WHERE pr_number = 7"))
        ).scalar_one()
    assert sha == "new-head"

    await review(client, auth, at("2026-11-22T10:00:03Z", "newest-head"))  # newer still wins
    assert [x["dep_name"] for x in await review_rows(client, auth)] == ["quietpkg2"]
    assert await redis.get(cache.GEN_KEY) != gen
