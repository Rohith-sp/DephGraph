"""Assert schema shape: 23 tables, 5 views, valid FKs, indexes (003 + 008), RLS, 005/007/009 columns."""
import asyncio
import re
import sys
from pathlib import Path

import asyncpg
from _db import DSN

MIGRATIONS = "\n".join(p.read_text() for p in sorted((Path(__file__).parents[1] / "apps/api/migrations").glob("*.sql")))
# Net index set: every CREATE INDEX in the migrations minus the ones a later migration drops (008).
DROPPED = set(re.findall(r"DROP INDEX (?:IF EXISTS )?(\w+)", MIGRATIONS))
INDEXES = sorted(set(re.findall(r"CREATE INDEX (?:IF NOT EXISTS )?(\w+)", MIGRATIONS)) - DROPPED)
COLUMNS = [("semantic_cluster", "keywords"), ("pr_risk_verdict", "reasoning"), ("maintainer", "last_active_at"), ("pr_review", "head_sha"), ("pr_review", "evaluated_at")]


async def main() -> int:
    c = await asyncpg.connect(DSN)
    try:
        return await check(c)
    finally:
        await c.close()


async def check(c: asyncpg.Connection) -> int:
    rows = lambda q, *a: c.fetch(q, *a)  # noqa: E731
    tables = await rows(
        "SELECT c.relname, c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r'"
    )
    views = await rows("SELECT viewname FROM pg_views WHERE schemaname = 'public' AND viewname LIKE 'v\\_%'")
    fks = await rows("SELECT conname, convalidated FROM pg_constraint WHERE contype = 'f'")
    idx = await rows("SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND indexname = ANY($1)", INDEXES)
    pol = await rows("SELECT DISTINCT tablename FROM pg_policies WHERE schemaname = 'public' AND cmd = 'SELECT'")
    gone = await rows("SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND indexname = ANY($1)", sorted(DROPPED))
    cols = await rows(
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'"
    )

    checks = [
        ("Tables", len(tables), 23),
        ("Views", len(views), 5),
        ("Indexes", len(idx), len(INDEXES)),
        ("Dropped indexes still present (008)", len(gone), 0),
        ("Columns from 005/007/009", sum((t, col) in {(r[0], r[1]) for r in cols} for t, col in COLUMNS), len(COLUMNS)),
        ("RLS enabled", sum(t["relrowsecurity"] for t in tables), 23),
        ("SELECT policies", len(pol), 23),
    ]
    ok = all(a == b for _, a, b in checks)
    fk_ok = bool(fks) and all(f["convalidated"] for f in fks)
    for name, a, b in checks:
        print(f"{'✅' if a == b else '❌'} {name}: {a}/{b}")
    print(f"{'✅' if fk_ok else '❌'} FK constraints: {'all valid' if fk_ok else 'invalid or missing'} ({len(fks)})")
    return 0 if ok and fk_ok else 1


sys.exit(asyncio.run(main()))
