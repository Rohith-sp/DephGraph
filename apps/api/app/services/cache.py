"""Read-through Redis cache for GET endpoints, invalidated per team and for the shared catalogue.

Data generations (Redis counters): one per team, one for the catalogue (vulnerabilities, packages,
clusters, maintainers: data every team's results derive from) and one for team memberships. A write
calls `bump()` after its commit for what it changed. A cache key embeds the catalogue generation and
the generation of every team in the caller's set, read BEFORE the value is computed, so a reader
racing a write stores under a generation nobody reads again and can never resurrect old data, and a
write for one team leaves other teams' entries alone. Redis errors never fail a request: reads
compute directly, and a failed bump leaves old entries to expire after TTL.

Generations and memberships are also held in-process for LOCAL_TTL seconds, so a burst of requests
shares one Redis read (Upstash bills per command). A write on the same instance updates its local
copy at once; on another instance it becomes visible within LOCAL_TTL.
"""

import time
from collections.abc import Awaitable, Callable, Collection, Iterable
from typing import TYPE_CHECKING, Generic, TypeVar

import structlog
from fastapi import Request, Response
from redis.exceptions import RedisError

from app.database import get_redis

if TYPE_CHECKING:
    from app.auth import Member

log = structlog.get_logger()
V = TypeVar("V")

CATALOG_KEY = "dephgraph:datagen:catalog"
MEMBERS_KEY = "dephgraph:datagen:members"
PREFIX = "dephgraph:cache:"
TTL = 120  # seconds; bounds staleness when a bump is lost or data changes outside the API
LOCAL_TTL = 1.5  # seconds


def team_key(team_id: int) -> str:
    return f"dephgraph:datagen:team:{team_id}"


class LocalTTL(Generic[V]):
    """Tiny per-process cache whose entries expire after LOCAL_TTL."""

    def __init__(self) -> None:
        self._items: dict[object, tuple[V, float]] = {}

    def get(self, key: object) -> V | None:
        found = self._items.get(key)
        return found[0] if found and found[1] > time.monotonic() else None

    def set(self, key: object, value: V) -> None:
        now = time.monotonic()
        if len(self._items) >= 4096:  # bounded: drop what has expired
            self._items = {k: v for k, v in self._items.items() if v[1] > now}
        self._items[key] = (value, now + LOCAL_TTL)

    def clear(self) -> None:
        self._items.clear()


generations: LocalTTL[int] = LocalTTL()
members: LocalTTL[frozenset[int]] = LocalTTL()  # user id -> team ids (filled by app.auth)


async def current(keys: list[str]) -> list[int]:
    """Generations of `keys` (0 when never bumped): local copy first, one MGET for the rest."""
    stale = [k for k in keys if generations.get(k) is None]
    if stale:
        for k, v in zip(stale, await get_redis().mget(stale)):
            generations.set(k, int(v or 0))
    return [generations.get(k) or 0 for k in keys]


async def bump(
    teams: Iterable[int] = (), *, catalog: bool = False, memberships: bool = False
) -> None:
    """Invalidate cached reads (call after the write has committed): the given teams' reads, and
    every team's when the catalogue changed. `memberships`: team memberships changed."""
    keys = [team_key(t) for t in sorted(set(teams))]
    keys += [k for k, on in ((CATALOG_KEY, catalog), (MEMBERS_KEY, memberships)) if on]
    if not keys:
        return
    if memberships:
        members.clear()
    try:
        async with get_redis().pipeline(transaction=False) as pipe:
            for k in keys:
                pipe.incr(k)
            for k, v in zip(keys, await pipe.execute()):
                generations.set(k, int(v))
    except (RedisError, OSError) as e:
        log.warning("cache_bump_failed", error=str(e))


async def read_through(
    name: str, build: Callable[[], Awaitable[str]], teams: Collection[int] = ()
) -> tuple[str, bool]:
    """(value, was_cached) for `name` under the current generations of `teams` + catalogue."""
    redis = get_redis()
    key: str | None = None
    try:
        ids = sorted(teams)
        gens = await current([CATALOG_KEY, *map(team_key, ids)])  # generations BEFORE computing
        key = PREFIX + ",".join(f"{k}={g}" for k, g in zip(["c", *ids], gens)) + f":{name}"
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
    request: Request,
    build: Callable[[], Awaitable[str]],
    member: "Member | None",
    ver: str = "",
) -> Response:
    """Envelope response cached per caller teams + path + query; `build` returns the JSON of `data`.

    Pass the caller for anything derived from team data, so one tenant's payload is never served
    to another; None for the catalogue (same for everyone). `build` also runs the authorization
    check: a 404 raised there is never cached. `ver` ("v2:") invalidates entries of an older
    payload shape.
    """

    async def envelope() -> str:
        return f'{{"data":{await build()},"error":null}}'

    scope = member.scope if member else "shared"
    body, _ = await read_through(
        f"{ver}{scope}:{request.url.path}?{request.url.query}",
        envelope,
        member.team_ids if member else (),
    )
    return Response(body, media_type="application/json")
