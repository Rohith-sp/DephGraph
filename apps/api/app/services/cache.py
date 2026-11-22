"""Read-through Redis cache for GET endpoints, invalidated by one global data generation.

Every write path calls `bump()` after its commit. Keys embed the generation read BEFORE the value
is computed, so a reader racing a write stores under a generation nobody reads again and can never
resurrect old data. Redis errors never fail a request: reads compute directly, and a failed bump
leaves old entries to expire after TTL.
"""

from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response
from redis.exceptions import RedisError

from app.database import get_redis

log = structlog.get_logger()

GEN_KEY = "dephgraph:datagen"
PREFIX = "dephgraph:cache:"
TTL = 120  # seconds; bounds staleness when a bump is lost or data changes outside the API


async def bump() -> None:
    """Invalidate every cached read (call after the write has committed)."""
    try:
        await get_redis().incr(GEN_KEY)
    except (RedisError, OSError) as e:
        log.warning("cache_bump_failed", error=str(e))


async def read_through(name: str, build: Callable[[], Awaitable[str]]) -> tuple[str, bool]:
    """(value, was_cached) for `name` under the current generation; `build` runs on a miss."""
    redis = get_redis()
    key: str | None = None
    try:
        key = f"{PREFIX}{await redis.get(GEN_KEY) or 0}:{name}"  # generation BEFORE computing
        if (hit := await redis.get(key)) is not None:
            return hit, True
    except (RedisError, OSError) as e:
        log.warning("cache_read_failed", error=str(e))
        key = None
    value = await build()
    if key:
        try:
            await redis.set(key, value, ex=TTL)
        except (RedisError, OSError) as e:
            log.warning("cache_write_failed", error=str(e))
    return value, False


async def cached_json(
    request: Request, build: Callable[[], Awaitable[str]], scope: str
) -> Response:
    """Envelope response cached per scope + path + query; `build` returns the JSON of `data`.

    `scope` is `member.scope` for anything derived from team data ("shared" for the catalogue), so
    one tenant's payload is never served to another. `build` also runs the authorization check:
    a 404 raised there is never cached.
    """

    async def envelope() -> str:
        return f'{{"data":{await build()},"error":null}}'

    body, _ = await read_through(f"{scope}:{request.url.path}?{request.url.query}", envelope)
    return Response(body, media_type="application/json")
