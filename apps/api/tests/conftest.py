"""Test harness: dedicated Postgres DB (dephgraph_test, migrated + seeded), Mongo db, Redis db 15.

Settings are overridden BEFORE app.database creates its clients.
"""

import re
import sys
import time
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from pymongo.errors import PyMongoError

from app.config import settings

ROOT = Path(__file__).resolve().parents[3]
TEST_DB = "dephgraph_test"
ADMIN_DSN = re.sub(r"/[^/?]+(\?|$)", r"/postgres\1", settings.database_url).replace(
    "postgresql+asyncpg://", "postgresql://"
)
TEST_DSN = re.sub(r"/[^/?]+(\?|$)", rf"/{TEST_DB}\1", settings.database_url).replace(
    "postgresql+asyncpg://", "postgresql://"
)

settings.database_url = TEST_DSN.replace("postgresql://", "postgresql+asyncpg://")
settings.mongo_db_name = TEST_DB
settings.redis_url = re.sub(r"/\d+$", "", settings.redis_url) + "/15"
settings.supabase_jwt_secret = "test-jwt-secret-at-least-32-characters"
settings.internal_cron_secret = "test-cron-secret"
settings.rate_limit = "1000000/minute"  # test_polish lowers it per test

sys.path.insert(0, str(ROOT / "scripts"))
import seed_db  # noqa: E402

from app.database import get_mongo, get_redis  # noqa: E402
from app.main import app  # noqa: E402
from app.models.mongo import ensure_indexes  # noqa: E402
from app.services import http  # noqa: E402


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """One session event loop for every async test: the app's engine/clients are module globals."""
    for item in items:
        if pytest_asyncio.is_async_test(item):
            item.add_marker(pytest.mark.asyncio(loop_scope="session"), append=False)


@pytest.fixture(scope="session", autouse=True)
async def database():
    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    await admin.execute(f"CREATE DATABASE {TEST_DB}")
    await admin.close()
    conn = await asyncpg.connect(TEST_DSN)
    for sql in sorted((ROOT / "apps/api/migrations").glob("*.sql")):
        await conn.execute(sql.read_text())
    await conn.close()
    await seed_db.main(TEST_DSN)
    try:
        await get_mongo().client.drop_database(TEST_DB)
        await ensure_indexes()
    except PyMongoError:
        pass
    yield


@pytest.fixture(autouse=True)
async def clean_stores(monkeypatch):
    monkeypatch.setattr(http, "BACKOFF", 0)
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
    await seed_db.main(TEST_DSN)


@pytest.fixture
def auth() -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": "00000000-0000-0000-0000-000000000001",
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
