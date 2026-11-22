"""scripts/migrate.py: fresh apply, no-op re-run, immutability, baseline for pre-runner databases."""

import shutil

import asyncpg
import migrate
import pytest

from tests.conftest import ADMIN_DSN, TEST_DB, TEST_DSN

SCRATCH = f"{TEST_DB}_migrate"


@pytest.fixture
async def conn(tmp_path, monkeypatch):
    """Empty database + a private copy of the migration files (tests edit them)."""
    shutil.copytree(migrate.MIGRATIONS, tmp_path / "m")
    monkeypatch.setattr(migrate, "MIGRATIONS", tmp_path / "m")
    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)")
    await admin.execute(f"CREATE DATABASE {SCRATCH}")
    c = await asyncpg.connect(TEST_DSN.replace(TEST_DB, SCRATCH))
    yield c
    await c.close()
    await admin.execute(f"DROP DATABASE {SCRATCH} WITH (FORCE)")
    await admin.close()


async def applied(c) -> list[str]:
    return [r[0] for r in await c.fetch("SELECT version FROM schema_migrations ORDER BY 1")]


async def test_fresh_apply_then_rerun_is_a_noop(conn):
    names = [p.name for p in sorted(migrate.MIGRATIONS.glob("*.sql"))]
    assert await migrate.migrate(conn, dry_run=True) == names
    assert await applied(conn) == []  # a dry run applies nothing
    assert await migrate.migrate(conn) == names
    assert await applied(conn) == [n.split("_")[0] for n in names]
    assert await migrate.migrate(conn) == []
    assert await conn.fetchval("SELECT to_regclass('public.pr_gate_delivery')") is not None


async def test_edited_migration_is_refused(conn):
    await migrate.migrate(conn)
    path = next(migrate.MIGRATIONS.glob("001_*.sql"))
    path.write_text(path.read_text() + "\n-- edited\n")
    with pytest.raises(migrate.MigrationError, match="001_initial_schema.sql"):
        await migrate.migrate(conn)


async def test_failed_migration_rolls_back_and_is_not_recorded(conn):
    await migrate.migrate(conn)
    (migrate.MIGRATIONS / "999_bad.sql").write_text("CREATE TABLE t_ok (a INT); SELECT 1/0;")
    with pytest.raises(asyncpg.DivisionByZeroError):
        await migrate.migrate(conn)
    assert await conn.fetchval("SELECT to_regclass('public.t_ok')") is None
    assert "999" not in await applied(conn)


async def test_existing_database_needs_baseline(conn):
    for p in sorted(migrate.MIGRATIONS.glob("*.sql"))[:10]:  # what the old initdb mount did
        await conn.execute(p.read_text())
    with pytest.raises(migrate.MigrationError, match="--baseline"):
        await migrate.migrate(conn)
    assert len(await migrate.baseline(conn, "010")) == 10
    assert await migrate.migrate(conn) == ["011_pr_gate_delivery.sql", "012_onboarding.sql"]
    with pytest.raises(migrate.MigrationError, match="already exists"):
        await migrate.baseline(conn, "010")


async def test_baseline_checks_the_schema_is_really_there(conn):
    await conn.execute(next(migrate.MIGRATIONS.glob("001_*.sql")).read_text())
    with pytest.raises(migrate.MigrationError, match="missing tables"):
        await migrate.baseline(conn, "010")
    with pytest.raises(migrate.MigrationError, match="no migration with version"):
        await migrate.baseline(conn, "777")
