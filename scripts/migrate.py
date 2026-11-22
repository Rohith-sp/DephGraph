"""Apply apps/api/migrations/*.sql in order, each in its own transaction, recorded in schema_migrations.

  python scripts/migrate.py             apply every pending migration
  python scripts/migrate.py --status    show how many are applied and which are pending
  python scripts/migrate.py --dry-run   list what would be applied, apply nothing
  python scripts/migrate.py --baseline 010   existing database that already has 001..010 (made by
                                        psql / the old initdb mount): record them without running

A migration file whose checksum differs from the one recorded when it was applied is refused:
applied files are immutable, schema changes go in a new numbered file. DATABASE_URL must be a direct
or session-pooler connection (the run holds an advisory lock), not Supabase's transaction pooler.
"""

import argparse
import asyncio
import hashlib
import re
import sys
from pathlib import Path

import asyncpg
from _db import DSN

MIGRATIONS = Path(__file__).resolve().parents[1] / "apps/api/migrations"
LOCK = 7_261_999  # pg_advisory_lock id: two deploys never migrate at once
# RLS without a policy: PostgREST (anon/authenticated) cannot read the ledger.
LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version    TEXT PRIMARY KEY,
  filename   TEXT NOT NULL,
  checksum   TEXT NOT NULL,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE schema_migrations ENABLE ROW LEVEL SECURITY;
"""
RECORD = "INSERT INTO schema_migrations (version, filename, checksum) VALUES ($1, $2, $3)"


class MigrationError(Exception):
    pass


def files() -> list[tuple[str, Path, str]]:
    """(version, path, sha256) for every migration file, in order."""
    return [
        (p.name.split("_", 1)[0], p, hashlib.sha256(p.read_bytes()).hexdigest())
        for p in sorted(MIGRATIONS.glob("*.sql"))
    ]


async def state(conn: asyncpg.Connection) -> tuple[dict[str, str], list[tuple[str, Path, str]]]:
    """(applied version -> checksum, pending files); MigrationError if an applied file changed."""
    await conn.execute(LEDGER)
    applied = {r["version"]: r["checksum"] for r in await conn.fetch("SELECT * FROM schema_migrations")}
    changed = [p.name for v, p, sha in files() if v in applied and applied[v] != sha]
    if changed:
        raise MigrationError(f"applied migration changed on disk (immutable): {', '.join(changed)}")
    return applied, [f for f in files() if f[0] not in applied]


async def migrate(conn: asyncpg.Connection, dry_run: bool = False) -> list[str]:
    """Apply pending migrations; the file names applied (or that would be, with dry_run)."""
    await conn.execute("SELECT pg_advisory_lock($1)", LOCK)
    try:
        applied, pending = await state(conn)
        if pending and not applied and await conn.fetchval("SELECT to_regclass('public.service')"):
            raise MigrationError(
                "database already has the schema but no migration history: "
                "run --baseline <latest applied version> (e.g. 010) first"
            )
        for version, path, sha in [] if dry_run else pending:
            async with conn.transaction():
                await conn.execute(path.read_text(encoding="utf-8"))
                await conn.execute(RECORD, version, path.name, sha)
        return [p.name for _, p, _ in pending]
    finally:
        await conn.execute("SELECT pg_advisory_unlock($1)", LOCK)


async def baseline(conn: asyncpg.Connection, upto: str) -> list[str]:
    """Record every migration up to `upto` as applied, after checking its tables exist."""
    await conn.execute(LEDGER)
    if await conn.fetchval("SELECT count(*) FROM schema_migrations"):
        raise MigrationError("migration history already exists; baseline is for first adoption")
    known = [f for f in files() if f[0] <= upto]
    if not any(v == upto for v, _, _ in known):
        raise MigrationError(f"no migration with version {upto}")
    present = {r[0] for r in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    wanted = {t for _, p, _ in known for t in re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)", p.read_text())}
    if missing := wanted - present:
        raise MigrationError(f"not applied up to {upto}, missing tables: {', '.join(sorted(missing))}")
    async with conn.transaction():
        await conn.executemany(RECORD, [(v, p.name, sha) for v, p, sha in known])
    return [p.name for _, p, _ in known]


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--baseline", metavar="VERSION")
    a = ap.parse_args()
    conn = await asyncpg.connect(DSN)
    try:
        if a.baseline:
            print("baselined:", *await baseline(conn, a.baseline))
        elif a.status:
            applied, pending = await state(conn)
            print(f"applied {len(applied)}, pending: {', '.join(p.name for _, p, _ in pending) or 'none'}")
        else:
            done = await migrate(conn, a.dry_run)
            print(("would apply:" if a.dry_run else "applied:"), *(done or ["nothing to do"]))
    except MigrationError as e:
        print(f"migrate: {e}", file=sys.stderr)
        return 1
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
