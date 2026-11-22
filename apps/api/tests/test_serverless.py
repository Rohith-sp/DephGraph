"""Serverless-safe runtime: slim imports, pooler-safe engine, Redis rate limit, membership cache."""

import json
import subprocess
import sys
from pathlib import Path

from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import text

from app import auth as auth_module
from app import limiter
from app.config import settings
from app.database import SessionLocal, get_redis
from app.services import cache
from tests.conftest import DEV_USER, ROOT

API_DIR = Path(ROOT / "apps/api")
ML = ("numpy", "torch", "sklearn", "sentence_transformers", "umap", "hdbscan")


PROD_ENV = dict(
    VERCEL="1",
    DATABASE_URL="postgresql+asyncpg://u:p@db.example.com:5432/d",
    REDIS_URL="rediss://r.example.com:6379",
    MONGO_URI="mongodb://m.example.com",
    ORG_ADMIN_EMAILS="a@b.co",
    GITHUB_WEBHOOK_SECRET="w" * 32,
    INTERNAL_CRON_SECRET="c" * 32,
)


def run_py(code: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=API_DIR,
        env={"PATH": "", "DATABASE_URL": settings.database_url, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_api_imports_without_ml_packages():
    blocked = ", ".join(f"{m!r}: None" for m in ML)  # None in sys.modules => ImportError
    r = run_py(f"import sys; sys.modules.update({{{blocked}}}); import app.main; print('ok')")
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stderr[-800:]


def test_serverless_engine_is_pooler_safe_and_works():
    r = run_py(
        "from app.database import engine; print(engine.pool.size(), engine.pool._pre_ping)",
        **PROD_ENV,  # VERCEL=1 is production: no localhost URLs, so the engine is never connected here
    )
    assert r.returncode == 0, r.stderr[-800:]
    assert r.stdout.split() == ["3", "True"]


async def test_pooler_connect_args_work_and_name_statements_uniquely():
    """Supavisor shares server connections: asyncpg's per-connection counter names collide there."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.database import POOLER_CONNECT_ARGS

    name = POOLER_CONNECT_ARGS["prepared_statement_name_func"]
    assert len({name() for _ in range(1000)}) == 1000
    e = create_async_engine(settings.database_url, connect_args=POOLER_CONNECT_ARGS)
    try:
        async with e.connect() as c:
            assert (await c.execute(text("SELECT CAST(:x AS INT)"), {"x": 1})).scalar_one() == 1
    finally:
        await e.dispose()


def test_default_engine_keeps_pool_settings():
    r = run_py("from app.database import engine; print(engine.pool.size(), engine.pool._pre_ping)")
    assert r.stdout.split() == ["10", "False"], r.stderr[-800:]


# ---- Redis fixed-window rate limit ---------------------------------------------------------------
async def test_rate_limit_counts_in_redis_with_a_ttl(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit", "2/minute")
    codes = [(await client.get("/services")).status_code for _ in range(3)]
    assert codes == [401, 401, 429]
    (key,) = [k async for k in get_redis().scan_iter(f"{limiter.PREFIX}*")]
    assert await get_redis().get(key) == "3" and 0 < await get_redis().ttl(key) <= 60


async def test_rate_limit_falls_back_to_memory_when_redis_is_down(client, monkeypatch):
    class Down:
        async def eval(self, *_a):
            raise RedisConnectionError("down")

    monkeypatch.setattr(limiter, "get_redis", lambda: Down())
    monkeypatch.setattr(settings, "rate_limit", "2/minute")
    monkeypatch.setattr(settings, "trusted_proxy_hops", 1)
    ip = {"X-Forwarded-For": "203.0.113.9"}  # a bucket of its own: the memory store is global
    codes = [(await client.get("/services", headers=ip)).status_code for _ in range(3)]
    assert codes == [401, 401, 429]


# ---- membership cache ----------------------------------------------------------------------------


async def test_membership_is_cached_until_the_generation_bumps(client, auth, reseed):
    assert (await client.get("/services", headers=auth)).status_code == 200
    key = f"dephgraph:member:{DEV_USER}"
    assert json.loads(await get_redis().get(key)) == [0, [1, 2, 3]]  # [generation, teams]
    assert 0 < await get_redis().ttl(key) <= auth_module.MEMBER_TTL

    async with SessionLocal() as s, s.begin():
        await s.execute(
            text("DELETE FROM team_member WHERE user_id = :u AND team_id = 3"), {"u": DEV_USER}
        )
    cache.members.clear()  # the in-process copy expired; Redis still has the old teams
    assert (await client.get("/services", headers=auth)).status_code == 200
    assert json.loads(await get_redis().get(key)) == [0, [1, 2, 3]]
    await cache.bump(memberships=True)  # what scripts/add_team_member.py does
    assert (await client.get("/services", headers=auth)).status_code == 200
    assert json.loads(await get_redis().get(key)) == [1, [1, 2]]


async def test_membership_falls_back_to_the_database_when_redis_is_down(client, auth, monkeypatch):
    class Down:
        async def mget(self, *_a):
            raise RedisConnectionError("down")

    monkeypatch.setattr(auth_module, "get_redis", lambda: Down())
    assert (await client.get("/services", headers=auth)).status_code == 200


async def test_member_lookup_and_handler_share_one_connection(client, auth, monkeypatch):
    """The lookup commits before the handler begins: a pool of one is enough."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from app import database

    one = create_async_engine(settings.database_url, pool_size=1, max_overflow=0, pool_timeout=3)
    monkeypatch.setattr(database.SessionLocal, "kw", {**database.SessionLocal.kw, "bind": one})
    assert (await client.get("/services", headers=auth)).status_code == 200
    await one.dispose()
