from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from redis.asyncio import Redis, from_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# All clients connect lazily: the app boots even if Mongo/Redis are down.
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
mongo_client: AsyncIOMotorClient = AsyncIOMotorClient(
    settings.mongo_uri, serverSelectionTimeoutMS=2000
)
redis_client: Redis = from_url(settings.redis_url, decode_responses=True)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_session)]


def get_mongo() -> AsyncIOMotorDatabase:
    return mongo_client[settings.mongo_db_name]


def get_redis() -> Redis:
    return redis_client


async def close_all() -> None:
    await engine.dispose()
    mongo_client.close()
    await redis_client.aclose()
