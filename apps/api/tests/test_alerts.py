"""GitHub Issue alerts: threshold, dedupe, isolation, close-on-fix (also when disabled or the service
is gone), ambiguous failures never duplicate, parked repos do not starve others, rate limits,
sanitised text, and PATCH /teams/{id}/alerts."""

import json

import httpx
import pytest
import respx
from sqlalchemy import text

from app.database import SessionLocal, get_redis
from app.services import alerts
from tests.github_app import API, configure_app, installed, not_installed
from tests.test_pr_gate import mkpkg

REPO = "acme/api-gateway"  # seeded service 1, team 1
REPO2 = "acme/auth-service"  # seeded service 2, team 1
ISSUES = f"{API}/repos/{REPO}/issues"


async def run_sql(sql: str, **p) -> None:
    async with SessionLocal() as s, s.begin():
        await s.execute(text(sql), p)


async def scalar(sql: str, **p):
    async with SessionLocal() as s:
        return (await s.execute(text(sql), p)).scalar_one()


@pytest.fixture(autouse=True)
async def _cfg(monkeypatch, reseed):
    configure_app(monkeypatch)
    monkeypatch.setattr(alerts, "PAUSE", 0)
    await run_sql("DELETE FROM affects")  # seeded exposures would add unrelated issues
    await run_sql("UPDATE team SET alerts_enabled = true WHERE team_id = 1")


async def expose(name: str, severity: str = "HIGH", service_id: int = 1) -> int:
    """Put a fresh vulnerable package into the service's latest production deployment."""
    await mkpkg(name, cvss=9.0)
    await run_sql(
        "UPDATE vulnerability SET severity = :s WHERE osv_id = :o", s=severity, o=f"T-{name}"
    )
    await link(name, service_id)
    return await scalar("SELECT vuln_id FROM vulnerability WHERE osv_id = :o", o=f"T-{name}")


async def link(name: str, service_id: int = 1) -> None:
    await run_sql(
        "INSERT INTO resolves_to (service_id, deploy_seq, package_id, version_string, depth, is_direct) "
        "SELECT :s, (SELECT max(deploy_seq) FROM deployment WHERE service_id = :s "
        "AND environment = 'production'), package_id, '1.0.0', 0, true FROM package WHERE name = :n",
        s=service_id,
        n=name,
    )


def issue_api(repo: str = REPO, number: int = 41):
    token = installed(repo)
    respx.post(f"{API}/repos/{repo}/labels").respond(201, json={})
    return token, respx.post(f"{API}/repos/{repo}/issues").respond(201, json={"number": number})


@respx.mock
async def test_opens_one_issue_then_dedupes():
    vid = await expose("alertdep")
    token, create = issue_api()
    await alerts.run([1])
    assert json.loads(token.calls[0].request.content) == {
        "repositories": ["api-gateway"],
        "permissions": {"issues": "write"},
    }
    sent = json.loads(create.calls[0].request.content)
    assert sent["title"] == "[DephGraph] T-alertdep (HIGH) in alertdep"
    assert sent["labels"] == ["dephgraph"] and "alertdep@1.0.0 (direct)" in sent["body"]
    assert "`alertdep >= 1.1.0`" in sent["body"] and "/vulnerabilities?q=T-alertdep" in sent["body"]
    row = await scalar(
        "SELECT issue_number || state || repo FROM alert_issue WHERE vuln_id = :v", v=vid
    )
    assert row == f"41open{REPO}"
    await alerts.run([1])
    assert create.call_count == 1


@respx.mock
async def test_threshold_disabled_default_and_other_team_are_ignored():
    await expose("lowdep", "MEDIUM")  # below the HIGH default
    _, create = issue_api()
    await alerts.run([1])
    assert not create.called
    await run_sql("UPDATE team SET alert_min_severity = 'MEDIUM' WHERE team_id = 1")
    await alerts.run([2])  # another team's run never touches team 1's service
    assert not create.called
    await run_sql("UPDATE team SET alerts_enabled = false WHERE team_id = 1")
    await alerts.run([1])
    assert not create.called
    await run_sql("UPDATE team SET alerts_enabled = true WHERE team_id = 1")
    await alerts.run()
    assert create.call_count == 1
    assert (
        await scalar("SELECT alerts_enabled FROM team WHERE team_id = 3") is False
    )  # off by default


@respx.mock
async def test_closes_when_fixed_even_if_alerts_were_switched_off_and_reopens_on_return():
    vid = await expose("fixdep")
    issue_api(number=7)
    await alerts.run([1])
    await run_sql(
        "DELETE FROM resolves_to WHERE package_id = (SELECT package_id FROM package WHERE name = 'fixdep')"
    )
    await run_sql("UPDATE team SET alerts_enabled = false WHERE team_id = 1")
    comment = respx.post(f"{ISSUES}/7/comments").respond(201, json={})
    patch = respx.patch(f"{ISSUES}/7").respond(200, json={})
    await alerts.run([1])
    assert json.loads(patch.calls[0].request.content)["state"] == "closed"
    assert "no longer reaches" in json.loads(comment.calls[0].request.content)["body"]
    assert await scalar("SELECT state FROM alert_issue WHERE vuln_id = :v", v=vid) == "closed"
    await run_sql("UPDATE team SET alerts_enabled = true WHERE team_id = 1")
    await link("fixdep")
    await alerts.run([1])  # exposed again: the same issue is reopened, not duplicated
    assert json.loads(patch.calls[1].request.content) == {"state": "open"}
    assert await scalar("SELECT state FROM alert_issue WHERE vuln_id = :v", v=vid) == "open"


@respx.mock
async def test_reopening_a_deleted_issue_creates_a_new_one():
    vid = await expose("gonedep")
    _, create = issue_api(number=7)
    await alerts.run([1])
    await run_sql("UPDATE alert_issue SET state = 'closed'")
    respx.post(f"{ISSUES}/7/comments").respond(404, json={})
    create.respond(201, json={"number": 8})
    await alerts.run([1])
    assert await scalar("SELECT issue_number FROM alert_issue WHERE vuln_id = :v", v=vid) == 8


@respx.mock
async def test_issue_of_a_deleted_service_is_still_closed():
    await expose("orphandep")
    issue_api(number=9)
    await alerts.run([1])
    await run_sql("DELETE FROM service WHERE service_id = 1")
    assert await scalar("SELECT service_id IS NULL FROM alert_issue") is True
    respx.post(f"{ISSUES}/9/comments").respond(201, json={})
    patch = respx.patch(f"{ISSUES}/9").respond(200, json={})
    await alerts.run([2])  # any run closes orphans
    assert patch.called and await scalar("SELECT state FROM alert_issue") == "closed"


@respx.mock
async def test_ambiguous_failure_never_duplicates_and_definite_failure_is_retried():
    await expose("flakydep")
    _, create = issue_api()
    create.respond(500)  # may have been created: sent once, placeholder kept
    await alerts.run([1])
    assert create.call_count == 1
    assert await scalar("SELECT state FROM alert_issue") == "opening"
    await alerts.run([1])
    assert create.call_count == 1  # no second POST while the placeholder stands
    await run_sql("UPDATE alert_issue SET opened_at = now() - interval '2 hours'")
    create.respond(422, json={})  # rejected: nothing created, retried next run
    await alerts.run([1])
    assert await scalar("SELECT count(*) FROM alert_issue") == 0
    create.respond(201, json={"number": 3})
    await alerts.run([1])
    assert await scalar("SELECT issue_number FROM alert_issue") == 3


@respx.mock
async def test_not_installed_repo_is_parked_and_does_not_use_the_write_budget(monkeypatch):
    monkeypatch.setattr(alerts, "MAX_WRITES", 1)
    await expose("firstdep", "CRITICAL", service_id=1)
    await expose("seconddep", "HIGH", service_id=2)
    not_installed(REPO)
    _, create = issue_api(REPO2, 5)
    await alerts.run([1])
    assert create.call_count == 1  # service 2 was not starved by service 1's missing install
    assert await scalar("SELECT until > now() FROM alert_skip WHERE repo = :r", r=REPO) is True
    respx.reset()
    await alerts.run([1])  # parked repo is not even looked up again
    assert not respx.calls


@respx.mock
async def test_forbidden_parks_the_repo_but_rate_limits_stop_the_run():
    await expose("forbiddendep", "CRITICAL", service_id=1)
    await expose("okdep", "HIGH", service_id=2)
    _, bad = issue_api(REPO, 1)
    bad.respond(403, json={})  # plain 403: a permission problem
    _, ok = issue_api(REPO2, 2)
    await alerts.run([1])
    assert ok.call_count == 1  # carried on to the next repo
    assert await scalar("SELECT count(*) FROM alert_skip WHERE repo = :r", r=REPO) == 1
    await run_sql("DELETE FROM alert_skip")
    await run_sql("DELETE FROM alert_issue")
    bad.respond(403, headers={"retry-after": "60"}, json={})
    ok.reset()
    await alerts.run([1])
    assert ok.call_count == 0  # rate limited on the first repo: stop for this run


@respx.mock
async def test_user_text_cannot_mention_or_inject():
    await run_sql("UPDATE service SET name = 'api @org/team `x` <b>' WHERE service_id = 1")
    await expose("@scope/pkgdep")
    _, create = issue_api()
    await alerts.run([1])
    sent = json.loads(create.calls[0].request.content)
    assert "@scope" not in sent["title"] and "@\u200dscope" in sent["title"]
    assert "@org" not in sent["body"] and "<b>" not in sent["body"] and "x" in sent["body"]


@respx.mock
async def test_a_second_concurrent_run_is_a_noop_and_unconfigured_app_does_nothing(monkeypatch):
    await expose("lockdep")
    _, create = issue_api()
    await get_redis().set(alerts.LOCK_KEY, "1", ex=60)
    await alerts.run([1])
    assert not create.called
    await get_redis().delete(alerts.LOCK_KEY)
    monkeypatch.setattr(alerts.github, "configured", lambda: False)
    await alerts.run([1])
    assert not create.called


def test_rate_limit_detection():
    def err(status, **h):
        r = httpx.Response(status, headers=h, request=httpx.Request("POST", "https://x"))
        return httpx.HTTPStatusError("x", request=r.request, response=r)

    assert alerts._rate_limited(err(429))
    assert alerts._rate_limited(err(403, **{"x-ratelimit-remaining": "0"}))
    assert not alerts._rate_limited(err(403, **{"x-ratelimit-remaining": "9"}))
    assert not alerts._rate_limited(httpx.ConnectError("x"))


async def test_patch_alerts_needs_team_admin(client, auth):
    r = await client.get("/teams/1/alerts", headers=auth)
    assert r.json()["data"] == {"enabled": True, "min_severity": "HIGH", "can_edit": True}
    r = await client.patch("/teams/1/alerts", json={"min_severity": "CRITICAL"}, headers=auth)
    assert r.status_code == 200 and r.json()["data"]["min_severity"] == "CRITICAL"
    r = await client.patch("/teams/1/alerts", json={"enabled": False}, headers=auth)
    assert r.json()["data"] == {"enabled": False, "min_severity": "CRITICAL", "can_edit": True}
    assert (
        await client.patch("/teams/1/alerts", json={"min_severity": "NONE"}, headers=auth)
    ).status_code == 422
    await run_sql("UPDATE team_member SET role = 'member' WHERE team_id = 1")
    assert (
        await client.patch("/teams/1/alerts", json={"enabled": True}, headers=auth)
    ).status_code == 403
    assert (await client.get("/teams/1/alerts", headers=auth)).json()["data"]["can_edit"] is False
    assert (
        await client.patch("/teams/999/alerts", json={"enabled": True}, headers=auth)
    ).status_code == 404
