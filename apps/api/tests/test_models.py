import re
from pathlib import Path

from sqlalchemy import text

from app.database import SessionLocal
from app.models.mongo import ensure_indexes
from app.models.sql import Base

APP = Path(__file__).parents[1] / "app"


async def test_orm_matches_migrations():
    """Every ORM table exists with exactly its columns, and every other table is used by raw SQL."""
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
    code = "\n".join(p.read_text() for p in APP.rglob("*.py"))
    unused = [
        t
        for t in db
        if t not in Base.metadata.tables
        and t != "schema_migrations"  # the migration runner's own ledger
        and not re.search(rf"\b{t}\b", code)
    ]
    assert not unused, f"tables neither mapped by the ORM nor used by any SQL in app/: {unused}"


async def test_ensure_indexes_idempotent():
    assert await ensure_indexes() is True
    assert await ensure_indexes() is True
