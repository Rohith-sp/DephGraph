import jwt
import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from limits import RateLimitItem, parse
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.auth import auth_configured, request_claims
from app.config import settings
from app.database import get_redis

log = structlog.get_logger()

# Fixed window in Redis, shared by every serverless instance: one atomic round trip, and the TTL is
# set on the first hit only (EXPIRE NX needs Redis 7, this works on any version).
_HIT = (
    "local n = redis.call('incr', KEYS[1]) "
    "if n == 1 then redis.call('expire', KEYS[1], ARGV[1]) end return n"
)
PREFIX = "dephgraph:rl:"
# shortcut: when Redis is down the limit falls back to per-process counters (rate_limit x instances);
# acceptable because a Redis outage already degrades the cache, upgrade if abuse shows up.
_fallback = MovingWindowRateLimiter(MemoryStorage())
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


async def limit_key(request: Request) -> str:
    """Verified JWT subject, else client IP.

    All traffic arrives via the Next.js server, so the IP is one shared bucket; the `sub` of a
    token whose signature checks out identifies the real user. Invalid/forged tokens fall back
    to the IP bucket, so a made-up `sub` cannot dodge the limit.
    """
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and token and auth_configured():
        try:
            if sub := (await request_claims(request, token)).get("sub"):
                return f"user:{sub}"
        except jwt.PyJWTError:
            pass
    return client_ip(request)


async def allowed(item: RateLimitItem, key: str) -> bool:
    try:
        hits = await get_redis().eval(_HIT, 1, f"{PREFIX}{key}", item.get_expiry())
        return int(hits) <= item.amount
    except (RedisError, OSError) as e:
        log.warning("rate_limit_redis_failed", error=str(e))
        return _fallback.hit(item, key)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-user/IP limit on every route (slowapi's middleware cannot see included routers)."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # limit is read per request so tests can change settings.rate_limit
        if request.url.path not in EXEMPT_PATHS and not await allowed(
            parse(settings.rate_limit), await limit_key(request)
        ):
            return JSONResponse({"data": None, "error": "Rate limit exceeded"}, 429)
        return await call_next(request)
