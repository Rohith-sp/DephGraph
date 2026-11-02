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
from app.database import SessionLocal, get_mongo
from app.services import github, pr_gate_engine
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


async def post(client, body: dict, event="pull_request", sig=None):
    raw = json.dumps(body).encode()
    return await client.post(
        "/pr-gate/webhook",
        content=raw,
        headers={
            "X-GitHub-Event": event,
            "X-Hub-Signature-256": sig or sign(raw),
            "Content-Type": "application/json",
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


async def test_unknown_repo_skipped(client):
    r = await post(client, payload(repo="https://github.com/other/thing"))
    assert r.status_code == 200 and r.json()["data"]["skipped"] is True


async def test_malformed_payload_422(client):
    assert (await post(client, {"action": "opened"})).status_code == 422


@respx.mock
async def test_spec_phase6_block_and_persist(client, auth, reseed):
    await mkpkg("evilcrit", cvss=9.8)
    files("evilcrit")
    r = await post(client, payload())
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["verdict"] in ["PASS", "ADVISORY", "BLOCK"] and data["verdict"] == "BLOCK"
    assert data["stage_reached"] in [1, 2, 3]
    assert data["processing_ms"] < 30_000
    assert [d["name"] for d in data["deps"]] == ["evilcrit"]

    v = (await client.get("/pr-gate/verdicts/1", headers=auth)).json()["data"]
    row = next(x for x in v if x["pr_number"] == 7)
    assert row["verdict"] == "BLOCK" and row["stage_reached"] == 2 and "CVSS" in row["reasoning"]


@respx.mock
async def test_stage1_pass_and_worst_verdict(client, reseed):
    await mkpkg("lowrisk", cvss=3.0)
    await mkpkg("copyleftpkg", vuln=False, copyleft=True)
    files("lowrisk", "unknownpkg")
    d = (await post(client, payload())).json()["data"]
    assert d["verdict"] == "PASS"
    by = {x["name"]: x for x in d["deps"]}
    assert by["unknownpkg"]["stage_reached"] == 1 and by["lowrisk"]["stage_reached"] == 2

    respx.clear()
    files("unknownpkg", "copyleftpkg")
    d = (await post(client, payload("synchronize"))).json()["data"]
    assert d["verdict"] == "BLOCK"  # copyleft reachable without any vuln


@respx.mock
async def test_stage3_groq_refines_advisory(client, monkeypatch, reseed):
    monkeypatch.setattr(settings, "groq_api_key", "gsk_test")
    await mkpkg("midrisk", cvss=7.0)
    files("midrisk")
    reply = groq_reply("BLOCK", "Exploitable in auth path.")
    route = respx.post(GROQ).respond(200, json=reply)
    d = (await post(client, payload())).json()["data"]
    assert d["verdict"] == "BLOCK" and d["stage_reached"] == 3
    assert d["deps"][0]["reasoning"] == "Exploitable in auth path."
    prompt = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert len(prompt) <= 1200
    stored = await get_mongo()["review_responses"].find_one({"dep_name": "midrisk"})
    assert stored and "BLOCK" in stored["response"]


@respx.mock
@pytest.mark.parametrize("key,status", [("", 500), ("gsk", 500), ("gsk", 429)])
async def test_groq_failure_keeps_stage2(client, monkeypatch, reseed, key, status):
    monkeypatch.setattr(settings, "groq_api_key", key)
    await mkpkg("midrisk2", cvss=7.0)
    files("midrisk2")
    respx.post(GROQ).respond(status)
    d = (await post(client, payload())).json()["data"]
    assert d["verdict"] == "ADVISORY" and d["stage_reached"] == 2


@respx.mock
async def test_groq_429_then_success(client, monkeypatch, reseed):
    monkeypatch.setattr(settings, "groq_api_key", "gsk")
    await mkpkg("midrisk3", cvss=7.0)
    files("midrisk3")
    respx.post(GROQ).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(200, json=groq_reply("ADVISORY", "ok")),
        ]
    )
    d = (await post(client, payload())).json()["data"]
    assert d["stage_reached"] == 3 and d["verdict"] == "ADVISORY"


@respx.mock
async def test_check_run_posted_when_configured(client, monkeypatch, reseed):
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
    assert (await post(client, payload())).json()["data"]["verdict"] == "BLOCK"
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
async def test_stage1_abandonment_boundaries(age, yanked, want):
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
    d = (await post(client, payload())).json()["data"]
    assert d["verdict"] == "BLOCK" and len(d["deps"]) == 2
    assert await _verdicts(client, auth) == {"dupe": "BLOCK"}  # PASS did not hide BLOCK

    respx.clear()  # synchronize: only the safe version remains -> stale BLOCK is gone
    patch = "@@ -1 +1,2 @@\n flask==2.0.0\n+dupe==2.0.0\n"
    respx.get(FILES).respond(200, json=[{"filename": "requirements.txt", "patch": patch}])
    await post(client, payload("synchronize"))
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
