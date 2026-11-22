from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from redis.asyncio import Redis, from_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# All clients connect lazily: the app boots even if Mongo/Redis are down.
# No pool_pre_ping (a ping round trip per checkout): connections idle past POOL_RECYCLE are replaced
# on checkout, and when one still dies SQLAlchemy invalidates the pool so only that request fails.
POOL_RECYCLE = 300
engine = create_async_engine(
    settings.database_url, pool_size=10, max_overflow=5, pool_recycle=POOL_RECYCLE
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
mongo_client: AsyncMongoClient = AsyncMongoClient(settings.mongo_uri, serverSelectionTimeoutMS=2000)
redis_client: Redis = from_url(settings.redis_url, decode_responses=True)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_session)]


def get_mongo() -> AsyncDatabase:
    return mongo_client[settings.mongo_db_name]


def get_redis() -> Redis:
    return redis_client


async def close_all() -> None:
    await engine.dispose()
    await mongo_client.close()
    await redis_client.aclose()
