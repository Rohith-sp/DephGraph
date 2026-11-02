from fastapi import Request
from fastapi.responses import JSONResponse
from jose import JWTError
from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.auth import decode_token
from app.config import settings

# shortcut: in-memory counters are correct only with a single worker (render.yaml: --workers 1);
# swap MemoryStorage for limits' RedisStorage(settings.redis_url) when scaling out.
storage = MemoryStorage()
_window = MovingWindowRateLimiter(storage)
# /health: keep-alive pings must never be throttled. /pr-gate/webhook: HMAC-verified (bad
# signatures 401 before any DB/network work) and GitHub drops 429'd deliveries.
EXEMPT_PATHS = {"/health", "/pr-gate/webhook"}


def client_ip(request: Request) -> str:
    """Client IP behind `trusted_proxy_hops` proxies.

    Trust assumption: each trusted proxy appends the address it saw to X-Forwarded-For, so the
    entry `hops` from the right is the first one we did not control. Anything left of it is
    client-supplied and spoofable, so it is never used. Without the header (or hops=0) we fall
    back to the socket peer.
    """
    hops = settings.trusted_proxy_hops
    parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if hops > 0 and len(parts) >= hops:
        return parts[-hops]
    return request.client.host if request.client else "unknown"


def limit_key(request: Request) -> str:
    """Verified JWT subject, else client IP.

    All traffic arrives via the Next.js server, so the IP is one shared bucket; the `sub` of a
    token whose signature checks out identifies the real user. Invalid/forged tokens fall back
    to the IP bucket, so a made-up `sub` cannot dodge the limit.
    """
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and token and settings.supabase_jwt_secret:
        try:
            if sub := decode_token(token).get("sub"):
                return f"user:{sub}"
        except JWTError:
            pass
    return client_ip(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-user/IP limit on every route (slowapi's middleware cannot see included routers)."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # limit is read per request so tests can change settings.rate_limit
        if request.url.path not in EXEMPT_PATHS and not _window.hit(
            parse(settings.rate_limit), limit_key(request)
        ):
            return JSONResponse({"data": None, "error": "Rate limit exceeded"}, 429)
        return await call_next(request)
