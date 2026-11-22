import json
import time
import uuid
from dataclasses import dataclass
from typing import Annotated, Any

import httpx
import jwt
import structlog
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import DbSession, get_redis
from app.services import cache

log = structlog.get_logger()
bearer = HTTPBearer(auto_error=False)


_jwks: dict[str, dict[str, Any]] = {}  # kid -> JWK
_jwks_fetched = 0.0
_JWKS_ALGS = {"ES256": "EC", "RS256": "RSA"}  # alg -> JWK kty
JWKS_COOLDOWN = 30  # seconds between refetches, so forged kids cannot hammer Supabase


async def _signing_key(kid: str) -> dict[str, Any]:
    """JWK for `kid` from the project's JWKS; refetched once (rate-limited) on an unknown kid."""
    global _jwks_fetched
    if kid not in _jwks and time.monotonic() - _jwks_fetched > JWKS_COOLDOWN:
        _jwks_fetched = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json")
                r.raise_for_status()
            _jwks.update({k["kid"]: k for k in r.json()["keys"]})
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise jwt.InvalidTokenError(f"JWKS fetch failed: {e}") from e
    if kid not in _jwks:
        raise jwt.InvalidTokenError("Unknown signing key")
    return _jwks[kid]


def auth_configured() -> bool:
    return bool(settings.supabase_jwt_secret or settings.supabase_url)


async def decode_token(token: str) -> dict[str, Any]:
    """Verify a Supabase access token (aud=authenticated); raises jwt.PyJWTError.

    Legacy projects sign HS256 with the shared secret; current ones sign ES256/RS256 and publish
    the public keys at /auth/v1/.well-known/jwks.json. The algorithm is pinned per key source and
    key type, so `none` and HS256-signed-with-a-public-key (algorithm confusion) never verify.
    """
    header = jwt.get_unverified_header(token)
    alg = header.get("alg")
    if not isinstance(alg, str):  # unverified header: a list/dict alg is unhashable
        raise jwt.InvalidAlgorithmError("Invalid alg")
    if alg == "HS256" and settings.supabase_jwt_secret:
        key: Any = settings.supabase_jwt_secret
    elif alg in _JWKS_ALGS and settings.supabase_url:
        kid = header.get("kid")
        if not isinstance(kid, str):  # unverified header: an unhashable kid would raise TypeError
            raise jwt.InvalidTokenError("Invalid kid")
        jwk = jwt.PyJWK(await _signing_key(kid))
        if _JWKS_ALGS[alg] != jwk.key_type:
            raise jwt.InvalidAlgorithmError("Algorithm does not match key type")
        key = jwk.key
    else:
        raise jwt.InvalidAlgorithmError(f"Unsupported token algorithm: {alg}")
    return jwt.decode(
        token, key, algorithms=[alg], audience="authenticated", options={"require": ["exp", "aud"]}
    )


async def request_claims(request: Request, token: str) -> dict[str, Any]:
    """Verified claims of `token`, decoded once per request (rate limiter + auth dependency)."""
    if (claims := getattr(request.state, "claims", None)) is None:
        claims = request.state.claims = await decode_token(token)
    return claims


async def authenticated_claims(
    request: Request, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> dict[str, Any]:
    """Verified claims of the bearer token, whatever the user's email domain (only /me uses this)."""
    if creds is None or not auth_configured():
        raise HTTPException(401, "Not authenticated")
    try:
        return await request_claims(request, creds.credentials)
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid token") from None


_EMAIL_TTL = 60  # seconds; the same staleness bound as the membership cache
_emails: dict[uuid.UUID, tuple[str | None, float]] = {}
_has_auth_users: bool | None = (
    None  # Supabase's auth.users exists (plain Postgres in compose/CI: no)
)


async def verified_email(claims: dict[str, Any], session: AsyncSession) -> str | None:
    """Lower-cased email of the caller, only when it is confirmed.

    user_metadata is writable by the user, so it is never trusted. On Supabase the email and its
    confirmation come from auth.users (Dashboard -> Auth -> "Confirm email" must be ON, or anyone can
    claim any address); without that table (compose, CI) the signed token's own email claim is used.
    In production the claim fallback does not exist: no auth.users = 503.
    """
    global _has_auth_users
    uid = user_id_of(claims)
    if _has_auth_users is None:
        async with session.begin():
            _has_auth_users = (
                await session.execute(text("SELECT to_regclass('auth.users') IS NOT NULL"))
            ).scalar_one()
    if not _has_auth_users and settings.production:
        log.error("auth_users_missing", detail="production needs Supabase auth.users; refusing")
        raise HTTPException(503, "Authentication backend unavailable")
    if not _has_auth_users:
        email = claims.get("email")
        return email.lower() if isinstance(email, str) else None
    hit = _emails.get(uid)
    if hit and hit[1] > time.monotonic():
        return hit[0]
    async with session.begin():
        found = (
            await session.execute(
                text(
                    "SELECT lower(email), email_confirmed_at IS NOT NULL "
                    "FROM auth.users WHERE id = :u"
                ),
                {"u": uid},
            )
        ).first()
    email = found[0] if found and found[1] and found[0] else None
    if len(_emails) >= 4096:
        _emails.clear()
    _emails[uid] = (email, time.monotonic() + _EMAIL_TTL)
    return email


def invite_only() -> bool:
    return bool(settings.org_admins)


async def is_org_admin(claims: dict[str, Any], session: AsyncSession) -> bool:
    """May create teams: everyone while ORG_ADMIN_EMAILS is unset (dev/CI), else listed emails."""
    if not invite_only():
        return True
    return (await verified_email(claims, session)) in settings.org_admins


async def domain_allowed(claims: dict[str, Any], session: AsyncSession) -> bool:
    """ALLOWED_EMAIL_DOMAINS gate: unset = everyone, else a confirmed email on one of the domains."""
    if not (domains := settings.email_domains):
        return True
    email = await verified_email(claims, session)
    return email is not None and email.rpartition("@")[2] in domains


async def get_current_user(
    claims: Annotated[dict[str, Any], Depends(authenticated_claims)], session: DbSession
) -> dict[str, Any]:
    """Verified claims of a user inside the allowed email domains (403 otherwise)."""
    if not await domain_allowed(claims, session):
        raise HTTPException(403, "Your email domain is not allowed")
    return claims


@dataclass(frozen=True)
class Member:
    """The caller and the teams whose data they may read or write."""

    user_id: uuid.UUID
    team_ids: frozenset[int]

    @property
    def scope(self) -> str:
        """Cache-key component: users with the same teams see the same payloads."""
        return "teams:" + ",".join(map(str, sorted(self.team_ids)))


MEMBER_TTL = 60  # seconds; bounds staleness when a membership changes without a cache bump


async def teams_of(user_id: uuid.UUID, session: DbSession) -> frozenset[int]:
    """Teams of `user_id` from team_member (empty = no team, not an error).

    Cached in Redis under the membership generation (the team endpoints and scripts/add_team_member.py bump it, so a new
    membership takes effect at once) and in-process for a moment (see app.services.cache). On a miss
    the lookup runs on the request's own session in a short transaction of its own, so the handler
    later opens its `session.begin()` on the same, already-pooled connection.
    """
    if (cached := cache.members.get(user_id)) is not None:
        return cached
    redis = get_redis()
    key: str | None = f"dephgraph:member:{user_id}"
    gen = 0
    try:
        stored_gen, hit = await redis.mget(cache.MEMBERS_KEY, key)  # generation read BEFORE the db
        gen = int(stored_gen or 0)
        if hit is not None and (entry := json.loads(hit))[0] == gen:
            teams = frozenset(entry[1])
            cache.members.set(user_id, teams)
            return teams
    except (RedisError, OSError) as e:
        log.warning("member_cache_read_failed", error=str(e))
        key = None
    async with session.begin():
        found = await session.execute(
            text("SELECT team_id FROM team_member WHERE user_id = :u"), {"u": user_id}
        )
        teams = frozenset(found.scalars())
    cache.members.set(user_id, teams)
    if key:
        try:
            await redis.set(key, json.dumps([gen, sorted(teams)]), ex=MEMBER_TTL)
        except (RedisError, OSError) as e:
            log.warning("member_cache_write_failed", error=str(e))
    return teams


def user_id_of(claims: dict[str, Any]) -> uuid.UUID:
    try:
        return uuid.UUID(str(claims.get("sub")))
    except ValueError:
        raise HTTPException(401, "Invalid token") from None


async def current_member(
    claims: Annotated[dict[str, Any], Depends(get_current_user)], session: DbSession
) -> Member:
    """The authenticated caller and their teams."""
    user_id = user_id_of(claims)
    teams = await teams_of(user_id, session)
    if not teams and not await is_org_admin(claims, session):
        raise HTTPException(403, "Access is by invitation: ask an admin to invite you")
    return Member(user_id, teams)


CurrentMember = Annotated[Member, Depends(current_member)]
