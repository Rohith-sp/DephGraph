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

from app.config import settings
from app.database import DbSession, get_redis
from app.services.cache import GEN_KEY

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


async def get_current_user(
    request: Request, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> dict[str, Any]:
    """Return the verified claims of the bearer token."""
    if creds is None or not auth_configured():
        raise HTTPException(401, "Not authenticated")
    try:
        return await request_claims(request, creds.credentials)
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid token") from None


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


async def current_member(
    claims: Annotated[dict[str, Any], Depends(get_current_user)], session: DbSession
) -> Member:
    """Teams of the authenticated user, from team_member (empty set = no team, not an error).

    Cached in Redis per data generation, so scripts/add_team_member.py (which bumps it) takes effect
    at once. On a miss the lookup runs on the request's own session in a short transaction of its
    own, so the handler later opens its `session.begin()` on the same, already-pooled connection.
    """
    try:
        user_id = uuid.UUID(str(claims.get("sub")))
    except ValueError:
        raise HTTPException(401, "Invalid token") from None
    redis = get_redis()
    key: str | None = None
    try:
        key = f"dephgraph:member:{user_id}:{await redis.get(GEN_KEY) or 0}"  # generation BEFORE db
        if (hit := await redis.get(key)) is not None:
            return Member(user_id, frozenset(json.loads(hit)))
    except (RedisError, OSError) as e:
        log.warning("member_cache_read_failed", error=str(e))
        key = None
    async with session.begin():
        found = await session.execute(
            text("SELECT team_id FROM team_member WHERE user_id = :u"), {"u": user_id}
        )
        team_ids = sorted(found.scalars())
    if key:
        try:
            await redis.set(key, json.dumps(team_ids), ex=MEMBER_TTL)
        except (RedisError, OSError) as e:
            log.warning("member_cache_write_failed", error=str(e))
    return Member(user_id, frozenset(team_ids))


CurrentMember = Annotated[Member, Depends(current_member)]
