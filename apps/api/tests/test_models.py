from sqlalchemy import text

from app.database import SessionLocal
from app.models.mongo import ensure_indexes
from app.models.sql import Base


async def test_orm_matches_migrations():
    assert len(Base.metadata.tables) == 23
    async with SessionLocal() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name IN "
                    "(SELECT tablename FROM pg_tables WHERE schemaname = 'public')"
                )
            )
        ).all()
    db: dict[str, set[str]] = {}
    for t, c in rows:
        db.setdefault(t, set()).add(c)
    for name, table in Base.metadata.tables.items():
        assert {c.name for c in table.columns} == db[name], name


async def test_ensure_indexes_idempotent():
    assert await ensure_indexes() is True
    assert await ensure_indexes() is True
