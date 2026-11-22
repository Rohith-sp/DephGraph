"""Test harness: dedicated Postgres DB (dephgraph_test, migrated + seeded), Mongo db, Redis db 15.

Settings are overridden BEFORE app.database creates its clients.
"""

import os
import re
import sys
import time
import uuid
from pathlib import Path

import asyncpg
import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pymongo.errors import PyMongoError

from app.config import settings

ROOT = Path(__file__).resolve().parents[3]
# Overridable so several test runs can share one Postgres/Mongo/Redis without clobbering each other.
TEST_DB = os.environ.get("DEPHGRAPH_TEST_DB", "dephgraph_test")
ADMIN_DSN = re.sub(r"/[^/?]+(\?|$)", r"/postgres\1", settings.database_url).replace(
    "postgresql+asyncpg://", "postgresql://"
)
TEST_DSN = re.sub(r"/[^/?]+(\?|$)", rf"/{TEST_DB}\1", settings.database_url).replace(
    "postgresql+asyncpg://", "postgresql://"
)

settings.database_url = TEST_DSN.replace("postgresql://", "postgresql+asyncpg://")
settings.mongo_db_name = TEST_DB
settings.redis_url = (
    re.sub(r"/\d+$", "", settings.redis_url) + "/" + os.environ.get("DEPHGRAPH_TEST_REDIS_DB", "15")
)
settings.supabase_jwt_secret = "test-jwt-secret-at-least-32-characters"
settings.internal_cron_secret = "test-cron-secret"
settings.rate_limit = "1000000/minute"  # test_polish lowers it per test
settings.gate_live_lookup = False  # no test reaches OSV / deps.dev; live tests are opt-in (live)
# A developer's .env.local may hold real integrations (GitHub App, Groq, Sentry, admin list): tests
# start without them and opt in per test, so results never depend on whose machine runs them.
for _name in (
    "github_app_id",
    "github_private_key_base64",
    "github_repository",
    "github_webhook_secret",
    "groq_api_key",
    "org_admin_emails",
    "allowed_email_domains",
    "sentry_dsn",
):
    setattr(settings, _name, "")

sys.path.insert(0, str(ROOT / "scripts"))
import migrate  # noqa: E402
import seed_db  # noqa: E402
from _db import DEV_USER_ID  # noqa: E402

from app.database import get_mongo, get_redis  # noqa: E402
from app.main import app  # noqa: E402
from app.models.mongo import ensure_indexes  # noqa: E402
from app.services import cache, http  # noqa: E402


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """One session event loop for every async test: the app's engine/clients are module globals.
    Tests marked `live` use the real internet and run only with DEPHGRAPH_LIVE=1."""
    live_off = pytest.mark.skip(reason="live network test: set DEPHGRAPH_LIVE=1")
    for item in items:
        if "live" in item.keywords and os.environ.get("DEPHGRAPH_LIVE") != "1":
            item.add_marker(live_off)
        if pytest_asyncio.is_async_test(item):
            item.add_marker(pytest.mark.asyncio(loop_scope="session"), append=False)


async def seed() -> None:
    """Seed data stands for versions the OSV sync has already checked (the gate trusts those)."""
    await seed_db.main(TEST_DSN)
    conn = await asyncpg.connect(TEST_DSN)
    await conn.execute("UPDATE package_version SET osv_checked_at = now()")
    await conn.close()


@pytest.fixture(scope="session", autouse=True)
async def database():
    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    await admin.execute(f"CREATE DATABASE {TEST_DB}")
    await admin.close()
    conn = await asyncpg.connect(TEST_DSN)
    await migrate.migrate(conn)
    await conn.close()
    await seed()
    try:
        await get_mongo().client.drop_database(TEST_DB)
        await ensure_indexes()
    except PyMongoError:
        pass
    yield


DEV_USER = uuid.UUID(DEV_USER_ID)  # member of every seeded team


@pytest.fixture(autouse=True)
async def clean_stores(monkeypatch):
    monkeypatch.setattr(http, "BACKOFF", 0)
    cache.generations.clear()  # in-process copies of what flushdb drops below
    cache.members.clear()
    try:
        await get_redis().flushdb()
        for name in ("raw_manifests", "osv_advisories"):
            await get_mongo()[name].delete_many({})
    except (PyMongoError, OSError):
        pass


@pytest.fixture
async def reseed():
    """Use in tests that mutate Postgres: restores seed data afterwards."""
    yield
    await seed()


@pytest.fixture
def auth() -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": DEV_USER_ID,
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        settings.supabase_jwt_secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
