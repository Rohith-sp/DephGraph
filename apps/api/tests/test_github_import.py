"""push / installation / installation_repositories deliveries and import_default_branch: manifests
are fetched at a commit through a scoped installation token and ingested as production, once per
(service, sha), only for the services the event belongs to."""

import json
import uuid
from pathlib import Path

import pytest
import respx
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal
from app.services import github, github_import
from tests.github_app import API, configure_app, installed, not_installed
from tests.test_pr_gate import SECRET, post
from tests.test_pr_gate_queue import CRON, make_due, row
from tests.upstream import mock_upstreams

FIXTURES = Path(__file__).parent / "fixtures"
REQS = (FIXTURES / "requirements_flask.txt").read_text()
REPO = "acme/api-gateway"  # seeded service 1 (team 1), root_path ''
SHA = "a" * 40


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    configure_app(monkeypatch)
    monkeypatch.setattr(settings, "github_webhook_secret", SECRET)


async def scalar(sql: str, **p):
    async with SessionLocal() as s:
        return (await s.execute(text(sql), p)).scalar_one()


async def run_sql(sql: str, **p) -> None:
    async with SessionLocal() as s, s.begin():
        await s.execute(text(sql), p)


async def deployments(service_id: int) -> int:
    return await scalar("SELECT count(*) FROM deployment WHERE service_id = :s", s=service_id)


async def add_service(name: str, root_path: str) -> int:
    async with SessionLocal() as s, s.begin():
        return (
            await s.execute(
                text(
                    "INSERT INTO service (team_id, name, repo_url, root_path) "
                    f"VALUES (1, :n, 'https://github.com/{REPO}', :r) RETURNING service_id"
                ),
                {"n": name, "r": root_path},
            )
        ).scalar_one()


def listing(router, root: str, names: list[str]) -> None:
    prefix = f"{root}/" if root else ""
    router.get(f"{API}/repos/{REPO}/contents/{root}".rstrip("/")).respond(
        200, json=[{"name": n, "path": prefix + n, "type": "file"} for n in names]
    )


def raw(router, path: str, content: str = REQS) -> respx.Route:
    return router.get(f"{API}/repos/{REPO}/contents/{path}").respond(200, text=content)


def head(router, sha: str = SHA, branch: str = "main") -> None:
    router.get(f"{API}/repos/{REPO}/git/ref/heads/{branch}").respond(
        200, json={"object": {"sha": sha}}
    )


def push(files=("requirements.txt",), *, sha=SHA, ref="refs/heads/main", **extra) -> dict:
    return {
        "ref": ref,
        "before": "b" * 40,
        "after": sha,
        "repository": {
            "id": 111,
            "full_name": REPO,
            "html_url": f"https://github.com/{REPO}",
            "default_branch": "main",
        },
        "installation": {"id": 5},
        "commits": [{"added": [], "modified": list(files), "removed": []}],
        **extra,
    }


def hdr() -> dict[str, str]:
    return {"X-GitHub-Delivery": uuid.uuid4().hex}


def ready(router, root="", names=("requirements.txt",)):
    """App installed, branch head = SHA, one manifest listed and readable."""
    token = installed(REPO, router=router)
    head(router)
    listing(router, root, list(names))
    raw(router, f"{root}/{names[0]}".lstrip("/"))
    return token


async def test_push_imports_once_per_sha_with_a_scoped_token(client, reseed):
    before = await deployments(1)
    with mock_upstreams() as router:
        token = ready(router)
        first = hdr()
        r = await post(client, push(), event="push", headers_extra=first)
        assert r.status_code == 200 and r.json()["data"] == {"services": 1, "imported": 1}
        assert (await row(first["X-GitHub-Delivery"]))["status"] == "done"
        assert await deployments(1) == before + 1
        env = await scalar(
            "SELECT environment FROM deployment WHERE service_id = 1 AND deploy_seq = :q",
            q=before + 1,
        )
        assert env == "production"
        assert json.loads(token.calls[0].request.content) == {
            "repositories": ["api-gateway"],
            "permissions": {"contents": "read"},
        }
        assert (await post(client, push(), event="push", headers_extra=first)).json()["data"] == {
            "duplicate": True
        }
        other = await post(client, push(), event="push", headers_extra=hdr())
        assert other.json()["data"] == {"services": 1, "imported": 0}  # same sha, new delivery
        assert await deployments(1) == before + 1
        assert await scalar("SELECT status FROM service_import WHERE service_id = 1") == "done"


@respx.mock
async def test_non_default_branch_and_delete_are_skipped(client):
    for body in (push(ref="refs/heads/feature"), push() | {"deleted": True}):
        r = await post(client, body, event="push")
        assert r.status_code == 200 and r.json()["data"] == {"skipped": True}
    r = await post(client, {"action": "removed"}, event="installation_repositories")
    assert r.json()["data"] == {"skipped": True}
    r = await post(client, {"action": "deleted"}, event="installation")
    assert r.json()["data"] == {"skipped": True}
    assert not respx.calls


async def test_malformed_events_are_422(client):
    for event in ("push", "installation", "installation_repositories"):
        assert (await post(client, {"zen": "x"}, event=event)).status_code == 422


async def test_stale_push_is_dropped_when_it_is_no_longer_the_branch_head(client, reseed):
    before = await deployments(1)
    with mock_upstreams() as router:
        installed(REPO, router=router)
        head(router, sha="c" * 40)  # a newer commit already landed
        r = await post(client, push(), event="push")
    assert r.json()["data"] == {"services": 1, "imported": 0}
    assert await deployments(1) == before


@respx.mock
async def test_unrelated_paths_touch_nothing_and_call_no_github(client, reseed):
    await add_service("mono-web", "web")
    body = push(("docs/readme.md", "src/requirements.txt", "web/sub/package.json", "x.json.bak"))
    r = await post(client, body, event="push")
    assert r.json()["data"] == {"services": 0, "imported": 0}
    assert not respx.calls


async def test_root_path_selects_the_service(client, reseed):
    mono = await add_service("mono-web", "web")
    before, mono_before = await deployments(1), await deployments(mono)
    with mock_upstreams() as router:
        installed(REPO, router=router)
        head(router)
        listing(router, "web", ["package.json", "requirements-dev.txt"])
        raw(router, "web/package.json", (FIXTURES / "package_react.json").read_text())
        dev = raw(router, "web/requirements-dev.txt")
        r = await post(client, push(("web/package.json",)), event="push")
    assert r.json()["data"] == {"services": 1, "imported": 1}
    assert await deployments(mono) == mono_before + 1 and await deployments(1) == before
    assert not dev.called  # dev requirement files are not production


@pytest.mark.parametrize(
    "extra",
    [
        {"commits": [{"added": [], "modified": ["docs/a.md"], "removed": []}] * 2048},
        {"commits": [], "before": "b" * 40},  # no commit details
        {"forced": True, "commits": [{"added": [], "modified": ["docs/a.md"], "removed": []}]},
    ],
)
async def test_incomplete_or_forced_pushes_import_anyway(client, reseed, extra):
    before = await deployments(1)
    with mock_upstreams() as router:
        ready(router)
        r = await post(client, push() | extra, event="push")
    assert r.json()["data"] == {"services": 1, "imported": 1}
    assert await deployments(1) == before + 1


async def test_service_is_matched_by_repo_id_and_installation(client, reseed):
    # service 1 belongs to repo id 111 / installation 5; 2 is unregistered in this payload
    await run_sql(
        "UPDATE service SET github_repo_id = 111, github_installation_id = 5 WHERE service_id = 1"
    )
    before = await deployments(1)
    with mock_upstreams() as router:  # the URL is the same but another installation sends it
        r = await post(client, push() | {"installation": {"id": 6}}, event="push")
        assert r.json()["data"] == {"services": 0, "imported": 0}
        other_repo = push()
        other_repo["repository"] = other_repo["repository"] | {"id": 222}
        r = await post(client, other_repo, event="push")  # renamed/transferred: id differs
        assert r.json()["data"] == {"services": 0, "imported": 0}
        ready(router)
        r = await post(client, push(), event="push")
    assert r.json()["data"] == {"services": 1, "imported": 1}
    assert await deployments(1) == before + 1


@respx.mock
async def test_app_not_installed_imports_nothing(client):
    not_installed(REPO)
    d = hdr()
    body = push()
    del body["installation"]
    r = await post(client, body, event="push", headers_extra=d)
    assert r.status_code == 200 and r.json()["data"] == {"services": 0, "imported": 0}
    assert (await row(d["X-GitHub-Delivery"]))["status"] == "done"


async def test_failed_fetch_releases_the_claim_and_is_retried_by_the_sweeper(client, reseed):
    before = await deployments(1)
    d = hdr()
    with mock_upstreams() as router:
        installed(REPO, router=router)
        head(router)
        router.get(f"{API}/repos/{REPO}/contents").respond(500)
        r = await post(client, push(), event="push", headers_extra=d)
        assert r.status_code == 502 and await deployments(1) == before
        assert await scalar("SELECT count(*) FROM service_import") == 0  # claim released
        assert (await row(d["X-GitHub-Delivery"]))["status"] == "pending"
    with mock_upstreams() as router:
        ready(router)
        await make_due(d["X-GitHub-Delivery"])
        swept = await client.post("/internal/pr-gate/retry", headers=CRON)
    assert swept.json()["data"]["processed"] == 1
    assert await deployments(1) == before + 1


async def test_a_fresh_claim_blocks_and_a_stale_one_is_retaken(client, reseed):
    await run_sql("INSERT INTO service_import (service_id, sha) VALUES (1, :h)", h=SHA)
    before = await deployments(1)
    with mock_upstreams() as router:
        ready(router)
        r = await post(client, push(), event="push")
        assert r.json()["data"] == {"services": 1, "imported": 0}  # another worker is importing
        await run_sql("UPDATE service_import SET started_at = now() - interval '11 minutes'")
        r = await post(client, push(), event="push", headers_extra=hdr())
    assert r.json()["data"] == {"services": 1, "imported": 1}
    assert await deployments(1) == before + 1


@pytest.mark.parametrize(
    "event,body",
    [
        ("installation_repositories", {"action": "added", "repositories_added": []}),
        ("installation", {"action": "created", "repositories": []}),
    ],
)
async def test_app_install_imports_default_branch(client, reseed, event, body):
    key = "repositories_added" if event == "installation_repositories" else "repositories"
    body = body | {
        "installation": {"id": 5},
        key: [{"id": 111, "full_name": REPO}, {"id": 9, "full_name": "acme/unregistered"}],
    }
    before = await deployments(1)
    with mock_upstreams() as router:
        installed(REPO, router=router)
        router.get(f"{API}/repos/{REPO}").respond(200, json={"default_branch": "trunk"})
        head(router, branch="trunk")
        listing(router, "", ["requirements.txt"])
        raw(router, "requirements.txt")
        r = await post(client, body, event=event)
    assert r.json()["data"] == {"services": 1, "imported": 1}
    assert await deployments(1) == before + 1
    assert await scalar("SELECT sha FROM service_import WHERE service_id = 1") == SHA


async def test_import_default_branch_statuses(reseed):
    async with SessionLocal() as s:
        with mock_upstreams() as router:
            installed(REPO, router=router)
            router.get(f"{API}/repos/{REPO}").respond(200, json={"default_branch": "main"})
            head(router)
            router.get(f"{API}/repos/{REPO}/contents").respond(404)  # no manifests at all
            got = await github_import.import_default_branch(s, 1)
            assert got["status"] == "imported" and got["manifests"] == 0
            assert (await github_import.import_default_branch(s, 1))["status"] == "unchanged"
        with respx.mock:
            not_installed("acme/auth-service")
            assert await github_import.import_default_branch(s, 2) == {"status": "not_installed"}
        async with s.begin():
            await s.execute(
                text("UPDATE service SET repo_url = 'https://gitlab.com/a/b' WHERE service_id = 3")
            )
        assert await github_import.import_default_branch(s, 3) == {"status": "no_repo"}
        with pytest.raises(Exception, match="Service not found"):
            await github_import.import_default_branch(s, 999)


async def test_unparseable_manifest_is_skipped_not_fatal(client, reseed):
    with mock_upstreams() as router:
        installed(REPO, router=router)
        head(router)
        listing(router, "", ["requirements.txt", "package.json"])
        raw(router, "requirements.txt", "# only comments\n")  # 422: no versioned packages
        raw(router, "package.json", "{not json")
        r = await post(client, push(("requirements.txt",)), event="push")
    assert r.json()["data"] == {"services": 1, "imported": 1}
    assert await scalar("SELECT status FROM service_import WHERE service_id = 1") == "done"


async def test_all_manifests_become_one_deployment_and_a_lockfile_beats_its_manifest(
    client, reseed
):
    before = await deployments(1)
    with mock_upstreams() as router:
        installed(REPO, router=router)
        head(router)
        listing(router, "", ["requirements.txt", "uv.lock", "package.json"])
        raw(router, "requirements.txt")  # shadowed by uv.lock
        raw(router, "uv.lock", (FIXTURES / "uv.lock").read_text())
        raw(router, "package.json", (FIXTURES / "package_react.json").read_text())
        r = await post(client, push(("uv.lock",)), event="push")
    assert r.json()["data"] == {"services": 1, "imported": 1}
    assert await deployments(1) == before + 1  # not one per file
    q = before + 1
    ecosystems = await scalar(
        "SELECT array_agg(DISTINCT p.ecosystem) FROM resolves_to r JOIN package p USING (package_id) "
        "WHERE r.service_id = 1 AND r.deploy_seq = :q",
        q=q,
    )
    assert sorted(ecosystems) == ["npm", "pypi"]
    edges = "SELECT count(*) FROM deployment_edge WHERE service_id = 1 AND deploy_seq = :q"
    assert await scalar(edges, q=q) > 0  # pypi came from the lockfile
    locked = "SELECT locked FROM deployment WHERE service_id = 1 AND deploy_seq = :q"
    assert await scalar(locked, q=q) is False  # package.json is a plain manifest


async def test_lockfile_only_import_is_a_locked_deployment(client, reseed):
    before = await deployments(1)
    with mock_upstreams() as router:
        installed(REPO, router=router)
        head(router)
        listing(router, "", ["requirements.txt", "uv.lock"])
        raw(router, "requirements.txt")
        raw(router, "uv.lock", (FIXTURES / "uv.lock").read_text())
        await post(client, push(("uv.lock",)), event="push")
    q = before + 1
    locked = "SELECT locked FROM deployment WHERE service_id = 1 AND deploy_seq = :q"
    assert await scalar(locked, q=q) is True
    names = await scalar(
        "SELECT array_agg(p.name) FROM resolves_to r JOIN package p USING (package_id) "
        "WHERE r.service_id = 1 AND r.deploy_seq = :q",
        q=q,
    )
    assert "flask" in names and "werkzeug" in names


async def test_create_service_imports_and_survives_an_import_failure(client, monkeypatch):
    from tests.test_onboarding import hdr, new_team

    monkeypatch.setattr(github, "app_jwt", lambda: "app-jwt")
    h = hdr(uuid.uuid4())
    tid = await new_team(client, h)
    ref = f"{API}/repos/acme/shop/git/ref/heads/main"
    with mock_upstreams() as router:
        installed("acme/shop", router=router, installation=77)
        info = {"id": 555, "default_branch": "main"}
        router.get(f"{API}/repos/acme/shop").respond(200, json=info)
        failing = router.get(ref).respond(500)
        body = {"team_id": tid, "repo_url": "https://github.com/acme/shop"}
        r = await client.post("/services", json=body, headers=h)
        assert r.status_code == 201 and r.json()["data"]["import_status"] == "failed"
        sid = r.json()["data"]["service_id"]
        failing.respond(200, json={"object": {"sha": SHA}})
        router.get(f"{API}/repos/acme/shop/contents/api").respond(404)
        moved = await client.patch(f"/services/{sid}", json={"root_path": "api"}, headers=h)
        assert moved.status_code == 200 and moved.json()["data"]["import_status"] == "imported"
        renamed = await client.patch(f"/services/{sid}", json={"name": "shop-api"}, headers=h)
        assert renamed.json()["data"]["import_status"] is None  # nothing moved: no re-import
