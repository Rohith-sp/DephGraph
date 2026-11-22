import time
from typing import Annotated, Any

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from jose.exceptions import JOSEError

from app.config import settings

bearer = HTTPBearer(auto_error=False)


_jwks: dict[str, dict[str, Any]] = {}  # kid -> JWK
_jwks_fetched = 0.0
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
            raise JWTError(f"JWKS fetch failed: {e}") from e
    if kid not in _jwks:
        raise JWTError("Unknown signing key")
    return _jwks[kid]


def auth_configured() -> bool:
    return bool(settings.supabase_jwt_secret or settings.supabase_url)


async def decode_token(token: str) -> dict[str, Any]:
    """Verify a Supabase access token (aud=authenticated); raises JWTError.

    Legacy projects sign HS256 with the shared secret; current ones sign ES256/RS256 and publish
    the public keys at /auth/v1/.well-known/jwks.json.
    """
    header = jwt.get_unverified_header(token)
    alg = header.get("alg")
    if alg == "HS256" and settings.supabase_jwt_secret:
        key: Any = settings.supabase_jwt_secret
    elif alg in ("ES256", "RS256") and settings.supabase_url:
        kid = header.get("kid")
        if not isinstance(kid, str):  # unverified header: an unhashable kid would raise TypeError
            raise JWTError("Invalid kid")
        key = await _signing_key(kid)
    else:
        raise JWTError(f"Unsupported token algorithm: {alg}")
    try:
        return jwt.decode(token, key, algorithms=[alg], audience="authenticated")
    except JOSEError as e:  # JWKError (alg/key-type mismatch) is not a JWTError
        raise JWTError(str(e)) from e


async def request_claims(request: Request, token: str) -> dict[str, Any]:
    """Verified claims of `token`, decoded once per request (rate limiter + auth dependency)."""
    if (claims := getattr(request.state, "claims", None)) is None:
        claims = request.state.claims = await decode_token(token)
    return claims


async def get_current_user(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict[str, Any]:
    """Return the verified claims of the bearer token."""
    if creds is None or not auth_configured():
        raise HTTPException(401, "Not authenticated")
    try:
        return await request_claims(request, creds.credentials)
    except JWTError:
        raise HTTPException(401, "Invalid token") from None
