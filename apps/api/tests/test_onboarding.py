"""Self-serve onboarding: email gate, teams, members, invites, services, service tokens.

Seed: user A (dev token) is admin of teams 1-3 (services 1-5). Each test starts from the seed again.
"""

import time
import uuid

import httpx
import jwt
import pytest
import respx
from sqlalchemy import text

from app import auth as auth_module
from app.config import Settings, settings
from app.database import SessionLocal
from app.services import github
from tests.conftest import DEV_USER

A = DEV_USER
SAMPLES = {"service_id": 1, "samples": [{"name": "requests", "version": "2.31.0", "exec_count": 3}]}


pytestmark = pytest.mark.usefixtures("reseed")


def hdr(user: uuid.UUID, email: str | None = None) -> dict[str, str]:
    claims = {"sub": str(user), "aud": "authenticated", "exp": int(time.time()) + 600}
    if email:
        claims["email"] = email
    token = jwt.encode(claims, settings.supabase_jwt_secret, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


async def sql(query: str, **p):
    async with SessionLocal() as s, s.begin():
        result = await s.execute(text(query), p)
        return [dict(r) for r in result.mappings()] if result.returns_rows else []


async def new_team(client, headers, slug="acme") -> int:
    r = await client.post("/teams", json={"name": slug.title(), "slug": slug}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["data"]["team_id"]


# ---- email domain gate -----------------------------------------------------------------------
async def test_domain_gate(client, monkeypatch):
    monkeypatch.setattr(settings, "allowed_email_domains", "Corp.com, @partner.org")
    u = uuid.uuid4()
    for headers in (hdr(u), hdr(u, "u@evil.com")):
        me = (await client.get("/me", headers=headers)).json()["data"]
        assert me["allowed"] is False and me["teams"] == []
        r = await client.get("/services", headers=headers)
        assert r.status_code == 403 and r.json()["error"]
        assert (
            await client.post("/teams", json={"name": "X", "slug": "xx"}, headers=headers)
        ).status_code == 403
    for ok_mail in ("U@corp.com", "u@partner.org"):
        assert (await client.get("/me", headers=hdr(u, ok_mail))).json()["data"]["allowed"] is True
        assert (await client.get("/services", headers=hdr(u, ok_mail))).status_code == 200
    assert (await client.get("/health")).status_code == 200


async def test_gate_off_by_default(client):
    me = (await client.get("/me", headers=hdr(uuid.uuid4()))).json()["data"]
    assert me["allowed"] is True


# ---- teams, members ----------------------------------------------------------------------------
async def test_create_team_makes_creator_admin(client):
    u = uuid.uuid4()
    assert (await client.get("/me", headers=hdr(u))).json()["data"]["teams"] == []
    tid = await new_team(client, hdr(u))
    assert (await client.get("/me", headers=hdr(u))).json()["data"]["teams"] == [
        {"team_id": tid, "name": "Acme", "slug": "acme", "role": "admin"}
    ]
    members = (await client.get(f"/teams/{tid}/members", headers=hdr(u))).json()["data"]
    assert [(m["user_id"], m["role"]) for m in members] == [(str(u), "admin")]


async def test_create_team_validation(client):
    h = hdr(uuid.uuid4())
    await new_team(client, h)
    dup = await client.post("/teams", json={"name": "Other", "slug": "acme"}, headers=h)
    assert dup.status_code == 409
    for bad in (
        {"name": "", "slug": "ok"},
        {"name": "N", "slug": "Bad Slug"},
        {"name": "N", "slug": "a"},
    ):
        assert (await client.post("/teams", json=bad, headers=h)).status_code == 422
    assert (await client.post("/teams", json={"name": "N", "slug": "ok"})).status_code == 401


async def test_last_admin_cannot_leave(client):
    u = uuid.uuid4()
    h = hdr(u)
    tid = await new_team(client, h)
    demote = await client.patch(f"/teams/{tid}/members/{u}", json={"role": "member"}, headers=h)
    assert demote.status_code == 409
    assert (await client.delete(f"/teams/{tid}/members/{u}", headers=h)).status_code == 409
    # with a second admin both are fine
    v = uuid.uuid4()
    await client.post(
        f"/teams/{tid}/invites", json={"email": "v@x.org", "role": "admin"}, headers=h
    )
    await client.get("/me", headers=hdr(v, "v@x.org"))
    assert (
        await client.patch(f"/teams/{tid}/members/{u}", json={"role": "member"}, headers=h)
    ).status_code == 200
    assert (await client.delete(f"/teams/{tid}/members/{u}", headers=hdr(v))).status_code == 200
    # u is gone at once (membership cache bumped), v is the sole admin again
    assert (await client.get(f"/teams/{tid}/members", headers=h)).status_code == 404
    assert (await client.delete(f"/teams/{tid}/members/{v}", headers=hdr(v))).status_code == 409


async def test_member_admin_rules(client):
    owner, plain, outsider = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    tid = await new_team(client, hdr(owner))
    await client.post(f"/teams/{tid}/invites", json={"email": "p@x.org"}, headers=hdr(owner))
    await client.get("/me", headers=hdr(plain, "p@x.org"))
    mine = hdr(plain)
    assert (
        await client.get(f"/teams/{tid}/members", headers=mine)
    ).status_code == 200  # any member reads
    for call in (
        client.patch(f"/teams/{tid}/members/{owner}", json={"role": "member"}, headers=mine),
        client.delete(f"/teams/{tid}/members/{owner}", headers=mine),
        client.get(f"/teams/{tid}/invites", headers=mine),
        client.post(f"/teams/{tid}/invites", json={"email": "z@x.org"}, headers=mine),
    ):
        assert (await call).status_code == 403
    for call in (  # a team the caller is not in does not exist for them
        client.get(f"/teams/{tid}/members", headers=hdr(outsider)),
        client.delete(f"/teams/{tid}/members/{owner}", headers=hdr(outsider)),
        client.get(f"/teams/{tid}/invites", headers=hdr(outsider)),
    ):
        assert (await call).status_code == 404
    missing = await client.delete(f"/teams/{tid}/members/{uuid.uuid4()}", headers=hdr(owner))
    assert missing.status_code == 404


# ---- invites -----------------------------------------------------------------------------------
async def test_invite_claimed_by_email(client):
    owner, joiner = uuid.uuid4(), uuid.uuid4()
    tid = await new_team(client, hdr(owner))
    r = await client.post(
        f"/teams/{tid}/invites",
        json={"email": " New@Example.ORG ", "role": "admin"},
        headers=hdr(owner),
    )
    assert r.status_code == 201 and r.json()["data"]["email"] == "new@example.org"
    listed = (await client.get(f"/teams/{tid}/invites", headers=hdr(owner))).json()["data"]
    assert [(i["email"], i["role"]) for i in listed] == [("new@example.org", "admin")]

    for headers in (hdr(joiner),):
        assert (await client.get("/me", headers=headers)).json()["data"]["teams"] == []
    assert len(await sql("SELECT 1 FROM team_invite")) == 1

    me = (await client.get("/me", headers=hdr(joiner, "NEW@example.org"))).json()["data"]
    assert [(t["team_id"], t["role"]) for t in me["teams"]] == [(tid, "admin")]
    assert await sql("SELECT 1 FROM team_invite") == []
    # the new membership works on the very next request (membership cache bumped)
    assert (await client.get(f"/teams/{tid}/members", headers=hdr(joiner))).status_code == 200
    assert len(await sql("SELECT 1 FROM team_member WHERE team_id = :t", t=tid)) == 2


async def test_invite_upsert_delete_and_validation(client):
    owner = uuid.uuid4()
    h = hdr(owner)
    tid = await new_team(client, h)
    await client.post(f"/teams/{tid}/invites", json={"email": "a@x.org"}, headers=h)
    await client.post(
        f"/teams/{tid}/invites", json={"email": "A@x.org", "role": "admin"}, headers=h
    )
    assert (await sql("SELECT role FROM team_invite"))[0]["role"] == "admin"
    assert (
        await client.post(f"/teams/{tid}/invites", json={"email": "nope"}, headers=h)
    ).status_code == 422
    assert (await client.delete(f"/teams/{tid}/invites/A@x.org", headers=h)).status_code == 200
    assert (await client.delete(f"/teams/{tid}/invites/a@x.org", headers=h)).status_code == 404


async def test_invite_does_not_demote_existing_member(client):
    owner = uuid.uuid4()
    tid = await new_team(client, hdr(owner))
    await client.post(f"/teams/{tid}/invites", json={"email": "o@x.org"}, headers=hdr(owner))
    await client.get("/me", headers=hdr(owner, "o@x.org"))
    assert (await sql("SELECT role FROM team_member WHERE user_id = :u", u=owner))[0][
        "role"
    ] == "admin"
    assert await sql("SELECT 1 FROM team_invite") == []


# ---- services ----------------------------------------------------------------------------------
async def test_service_crud(client):
    owner = uuid.uuid4()
    h = hdr(owner)
    tid = await new_team(client, h)
    before = (await client.get("/services", headers=h)).json()["data"]
    assert before == []  # also fills the read cache

    body = {
        "team_id": tid,
        "repo_url": " HTTP://GitHub.com/Acme/Shop.git/ ",
        "root_path": "/apps/web/",
    }
    r = await client.post("/services", json=body, headers=h)
    assert r.status_code == 201, r.text
    svc = r.json()["data"]
    assert svc["repo_url"] == "https://github.com/acme/shop"
    assert (svc["name"], svc["root_path"]) == ("shop/apps/web", "apps/web")
    listed = (await client.get("/services", headers=h)).json()["data"]  # cache was bumped
    assert [(s["service_id"], s["root_path"]) for s in listed] == [(svc["service_id"], "apps/web")]

    # same repo, other directory is a second service; same repo + directory is a conflict
    other = await client.post("/services", json={**body, "root_path": "apps/api"}, headers=h)
    assert other.status_code == 201
    assert (await client.post("/services", json=body, headers=h)).status_code == 409
    assert (
        await client.post("/services", json={**body, "root_path": ""}, headers=h)
    ).status_code == 201

    sid = svc["service_id"]
    patched = await client.patch(
        f"/services/{sid}", json={"name": "Web", "root_path": "web"}, headers=h
    )
    assert patched.json()["data"]["name"] == "Web" and patched.json()["data"]["root_path"] == "web"
    clash = await client.patch(f"/services/{sid}", json={"root_path": "apps/api"}, headers=h)
    assert clash.status_code == 409
    assert (await client.patch(f"/services/{sid}", json={}, headers=h)).status_code == 422

    assert (await client.delete(f"/services/{sid}", headers=h)).status_code == 200
    assert (await client.delete(f"/services/{sid}", headers=h)).status_code == 404
    left = (await client.get("/services", headers=h)).json()["data"]
    assert sid not in [s["service_id"] for s in left]


@pytest.mark.parametrize(
    "extra",
    [
        {"repo_url": "https://gitlab.com/a/b"},
        {"repo_url": "https://github.com/a"},
        {"repo_url": "https://github.com/a/b/c"},
        {"root_path": "../etc"},
        {"root_path": "a/../b"},
        {"root_path": "a//b"},
        {"root_path": "a b"},
        {"name": " "},
    ],
)
async def test_service_validation(client, extra):
    h = hdr(uuid.uuid4())
    tid = await new_team(client, h)
    body = {"team_id": tid, "repo_url": "https://github.com/a/b", **extra}
    assert (await client.post("/services", json=body, headers=h)).status_code == 422


async def test_service_admin_only_and_tenant_scoped(client):
    owner, plain, outsider = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    tid = await new_team(client, hdr(owner))
    await client.post(f"/teams/{tid}/invites", json={"email": "p@x.org"}, headers=hdr(owner))
    await client.get("/me", headers=hdr(plain, "p@x.org"))
    body = {"team_id": tid, "repo_url": "https://github.com/a/b"}
    assert (await client.post("/services", json=body, headers=hdr(plain))).status_code == 403
    assert (await client.post("/services", json=body, headers=hdr(outsider))).status_code == 404
    sid = (await client.post("/services", json=body, headers=hdr(owner))).json()["data"][
        "service_id"
    ]
    for call in (
        client.patch(f"/services/{sid}", json={"name": "x"}, headers=hdr(plain)),
        client.delete(f"/services/{sid}", headers=hdr(plain)),
        client.post(f"/services/{sid}/tokens", json={"name": "t"}, headers=hdr(plain)),
        client.get(f"/services/{sid}/tokens", headers=hdr(plain)),
    ):
        assert (await call).status_code == 403
    for call in (
        client.patch(f"/services/{sid}", json={"name": "x"}, headers=hdr(outsider)),
        client.delete(f"/services/{sid}", headers=hdr(outsider)),
        client.post(f"/services/{sid}/tokens", json={"name": "t"}, headers=hdr(outsider)),
    ):
        assert (await call).status_code == 404


# ---- service tokens ----------------------------------------------------------------------------
async def make_token(client, auth, service_id=1) -> dict:
    r = await client.post(f"/services/{service_id}/tokens", json={"name": "ci"}, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()["data"]


async def test_token_ingests_for_its_service_only(client, auth):
    tok = await make_token(client, auth)
    assert tok["token"].startswith("dgs_") and len(tok["token"]) >= 40
    stored = await sql("SELECT token_hash, last_used_at FROM service_token")
    assert tok["token"] not in stored[0]["token_hash"] and stored[0]["last_used_at"] is None

    bearer = {"Authorization": f"Bearer {tok['token']}"}
    used = await client.post("/runtime/samples", json=SAMPLES, headers=bearer)
    assert used.status_code == 200, used.text
    assert (await sql("SELECT last_used_at FROM service_token"))[0]["last_used_at"] is not None
    other = await client.post("/runtime/samples", json={**SAMPLES, "service_id": 2}, headers=bearer)
    assert other.status_code == 403
    # a token is no user: it opens nothing else
    assert (await client.get("/services", headers=bearer)).status_code == 401


async def test_token_list_revoke_and_garbage(client, auth):
    tok = await make_token(client, auth)
    listed = (await client.get("/services/1/tokens", headers=auth)).json()["data"]
    assert [t["name"] for t in listed] == ["ci"]
    assert "token" not in listed[0] and "token_hash" not in listed[0]

    assert (
        await client.delete(f"/services/1/tokens/{tok['token_id']}", headers=auth)
    ).status_code == 200
    assert (
        await client.delete(f"/services/1/tokens/{tok['token_id']}", headers=auth)
    ).status_code == 200
    assert (await client.delete("/services/1/tokens/999999", headers=auth)).status_code == 404
    assert (
        await client.delete(f"/services/2/tokens/{tok['token_id']}", headers=auth)
    ).status_code == 404
    revoked = {"Authorization": f"Bearer {tok['token']}"}
    assert (await client.post("/runtime/samples", json=SAMPLES, headers=revoked)).status_code == 401
    for bad in ("Bearer dgs_nope", "Bearer dgs_", "Bearer dgs_" + "x" * 5000):
        r = await client.post("/runtime/samples", json=SAMPLES, headers={"Authorization": bad})
        assert r.status_code == 401
    assert (await client.post("/runtime/samples", json=SAMPLES)).status_code == 401
    # user JWTs still work on the same route
    assert (await client.post("/runtime/samples", json=SAMPLES, headers=auth)).status_code == 200


async def test_tokens_die_with_their_service(client, auth):
    await make_token(client, auth)
    assert (await client.delete("/services/1", headers=auth)).status_code == 200
    assert await sql("SELECT 1 FROM service_token") == []


# ---- confirmed email comes from auth.users, never from user_metadata ----------------------------
@pytest.fixture
async def auth_users():
    """Supabase's auth.users on the test database (plain Postgres has none)."""

    async def reset():
        auth_module._has_auth_users = None
        auth_module._emails.clear()

    await reset()
    await sql("DROP TABLE IF EXISTS auth.users")
    await sql(
        "CREATE TABLE auth.users (id uuid PRIMARY KEY, email text, email_confirmed_at timestamptz)"
    )
    yield
    await sql("DROP TABLE auth.users")
    await reset()


async def test_email_comes_from_auth_users(client, auth_users, monkeypatch):
    owner, joiner = uuid.uuid4(), uuid.uuid4()
    tid = await new_team(client, hdr(owner))
    await client.post(
        f"/teams/{tid}/invites", json={"email": "new@example.org"}, headers=hdr(owner)
    )
    forged = {  # the user wrote email_verified and the email into their own metadata and token
        "Authorization": "Bearer "
        + jwt.encode(
            {
                "sub": str(joiner),
                "aud": "authenticated",
                "exp": int(time.time()) + 600,
                "email": "new@example.org",
                "user_metadata": {"email_verified": True},
            },
            settings.supabase_jwt_secret,
            algorithm="HS256",
        )
    }
    await sql(
        "INSERT INTO auth.users VALUES (:u, 'New@Example.org', NULL)", u=joiner
    )  # unconfirmed
    assert (await client.get("/me", headers=forged)).json()["data"]["teams"] == []
    assert len(await sql("SELECT 1 FROM team_invite")) == 1
    await sql("UPDATE auth.users SET email_confirmed_at = now() WHERE id = :u", u=joiner)
    auth_module._emails.clear()  # the lookup is cached for a minute
    me = (await client.get("/me", headers=forged)).json()["data"]
    assert [t["team_id"] for t in me["teams"]] == [tid]
    members = (await client.get(f"/teams/{tid}/members", headers=forged)).json()["data"]
    assert sorted(m["email"] or "" for m in members) == [
        "",
        "new@example.org",
    ]  # owner has no email


async def test_domain_gate_needs_confirmed_email(client, auth_users, monkeypatch):
    monkeypatch.setattr(settings, "allowed_email_domains", "corp.com")
    u = uuid.uuid4()
    await sql("INSERT INTO auth.users VALUES (:u, 'u@corp.com', NULL)", u=u)
    me = (await client.get("/me", headers=hdr(u, "u@corp.com"))).json()["data"]
    assert me["allowed"] is False
    await sql("UPDATE auth.users SET email_confirmed_at = now() WHERE id = :u", u=u)
    auth_module._emails.clear()
    assert (await client.get("/me", headers=hdr(u, "u@corp.com"))).json()["data"]["allowed"] is True


# ---- invite-only access ------------------------------------------------------------------------
@pytest.fixture
def org(monkeypatch):
    monkeypatch.setattr(settings, "org_admin_emails", "Boss@Corp.com")


async def test_invite_only_access(client, org):
    boss, stranger = hdr(uuid.uuid4(), "boss@corp.com"), hdr(uuid.uuid4(), "x@corp.com")
    me = (await client.get("/me", headers=stranger)).json()["data"]
    assert me["allowed"] is False and me["org_admin"] is False and "invit" in me["reason"]
    for call in (
        client.get("/services", headers=stranger),
        client.post("/teams", json={"name": "N", "slug": "nn"}, headers=stranger),
    ):
        assert (await call).status_code == 403

    me = (await client.get("/me", headers=boss)).json()["data"]
    assert me["allowed"] is True and me["org_admin"] is True and me["teams"] == []
    tid = await new_team(client, boss)
    members = (await client.get(f"/teams/{tid}/members", headers=boss)).json()["data"]
    assert members[0]["email"] == "boss@corp.com"

    # invited people get in, but a plain team admin still cannot create teams
    await client.post(
        f"/teams/{tid}/invites", json={"email": "x@corp.com", "role": "admin"}, headers=boss
    )
    me = (await client.get("/me", headers=stranger)).json()["data"]
    assert me["allowed"] is True and [t["team_id"] for t in me["teams"]] == [tid]
    assert (await client.get("/services", headers=stranger)).status_code == 200
    denied = await client.post("/teams", json={"name": "N", "slug": "nn"}, headers=stranger)
    assert denied.status_code == 403


def test_production_requires_org_admins():
    with pytest.raises(ValueError, match="ORG_ADMIN_EMAILS"):
        Settings(environment="production", org_admin_emails="", _env_file=None)
    Settings(environment="production", org_admin_emails="a@b.co", _env_file=None)


# ---- invite expiry, email validation -----------------------------------------------------------
async def test_expired_invite_is_not_claimed(client):
    owner, joiner = uuid.uuid4(), uuid.uuid4()
    tid = await new_team(client, hdr(owner))
    await client.post(f"/teams/{tid}/invites", json={"email": "late@x.org"}, headers=hdr(owner))
    listed = (await client.get(f"/teams/{tid}/invites", headers=hdr(owner))).json()["data"]
    assert listed[0]["expires_at"] > listed[0]["created_at"]
    await sql("UPDATE team_invite SET expires_at = now() - interval '1 minute'")
    assert (await client.get(f"/teams/{tid}/invites", headers=hdr(owner))).json()["data"] == []
    assert (await client.get("/me", headers=hdr(joiner, "late@x.org"))).json()["data"][
        "teams"
    ] == []
    assert await sql("SELECT 1 FROM team_invite") == []  # swept, not honoured
    # inviting again renews
    renewed = await client.post(
        f"/teams/{tid}/invites", json={"email": "late@x.org"}, headers=hdr(owner)
    )
    assert renewed.status_code == 201


@pytest.mark.parametrize("email", ["j\u00f6rg@x.org", "a@b\u00e9.org", "a b@x.org"])
async def test_invite_email_ascii_only(client, email):
    h = hdr(uuid.uuid4())
    tid = await new_team(client, h)
    assert (
        await client.post(f"/teams/{tid}/invites", json={"email": email}, headers=h)
    ).status_code == 422


# ---- repository ownership ----------------------------------------------------------------------
async def test_repo_must_have_the_app_installed(client, monkeypatch):
    monkeypatch.setattr(settings, "github_app_id", "1")
    monkeypatch.setattr(settings, "github_private_key_base64", "x")
    monkeypatch.setattr(github, "app_jwt", lambda: "app-jwt")
    h = hdr(uuid.uuid4())
    tid, other = await new_team(client, h), await new_team(client, h, "other")
    body = {"team_id": tid, "repo_url": "https://github.com/Acme/Shop.git"}
    with respx.mock(assert_all_called=False) as gh:
        gh.get("https://api.github.com/repos/acme/shop/installation").respond(404)
        refused = await client.post("/services", json=body, headers=h)
        assert (
            refused.status_code == 422
            and "Install the DephGraph GitHub App on acme/shop" in refused.json()["error"]
        )
        assert await sql("SELECT 1 FROM service WHERE repo_url LIKE '%shop'") == []

        gh.get("https://api.github.com/repos/acme/shop/installation").respond(200, json={"id": 77})
        gh.post("https://api.github.com/app/installations/77/access_tokens").respond(
            201, json={"token": "t"}
        )
        gh.get("https://api.github.com/repos/acme/shop").respond(200, json={"id": 555})
        created = await client.post("/services", json=body, headers=h)
        assert created.status_code == 201, created.text
        assert set(created.json()["data"]) == {
            "service_id",
            "team_id",
            "name",
            "repo_url",
            "root_path",
        }
        row = (
            await sql(
                "SELECT github_installation_id AS i, github_repo_id AS r FROM service WHERE repo_url LIKE '%shop'"
            )
        )[0]
        assert (row["i"], row["r"]) == (77, 555)
        # the same repository cannot be registered again by another team, or twice
        clash = await client.post("/services", json={**body, "team_id": other}, headers=h)
        assert clash.status_code == 409
        # PATCH of repo_url is verified the same way
        gh.get("https://api.github.com/repos/acme/elsewhere/installation").respond(404)
        sid = created.json()["data"]["service_id"]
        moved = await client.patch(
            f"/services/{sid}", json={"repo_url": "https://github.com/acme/elsewhere"}, headers=h
        )
        assert moved.status_code == 422
        gh.get("https://api.github.com/repos/acme/shop2/installation").mock(
            side_effect=httpx.ConnectError("down")
        )
        down = await client.patch(
            f"/services/{sid}", json={"repo_url": "https://github.com/acme/shop2"}, headers=h
        )
        assert down.status_code == 502


async def test_repo_unique_across_teams_without_app(client):
    h = hdr(uuid.uuid4())
    tid, other = await new_team(client, h), await new_team(client, h, "other")
    body = {"team_id": tid, "repo_url": "https://github.com/a/b"}
    assert (await client.post("/services", json=body, headers=h)).status_code == 201
    assert (
        await client.post(
            "/services",
            json={**body, "team_id": other, "repo_url": "HTTPS://github.com/A/B/"},
            headers=h,
        )
    ).status_code == 409
    assert (
        await sql(
            "SELECT github_repo_id AS r FROM service WHERE repo_url = 'https://github.com/a/b'"
        )
    )[0]["r"] is None
