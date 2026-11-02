"""Assert schema shape: 23 tables, 5 views, valid FKs, 14 indexes, RLS on every table."""
import asyncio
import re
import sys
from pathlib import Path

import asyncpg
from _db import DSN

INDEXES = re.findall(
    r"CREATE INDEX (\w+)", (Path(__file__).parents[1] / "apps/api/migrations/003_indexes.sql").read_text()
)


async def main() -> int:
    c = await asyncpg.connect(DSN)
    rows = lambda q, *a: c.fetch(q, *a)  # noqa: E731
    tables = await rows(
        "SELECT c.relname, c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r'"
    )
    views = await rows("SELECT viewname FROM pg_views WHERE schemaname = 'public' AND viewname LIKE 'v\\_%'")
    fks = await rows("SELECT conname, convalidated FROM pg_constraint WHERE contype = 'f'")
    idx = await rows("SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND indexname = ANY($1)", INDEXES)
    pol = await rows("SELECT DISTINCT tablename FROM pg_policies WHERE schemaname = 'public' AND cmd = 'SELECT'")
    await c.close()

    checks = [
        ("Tables", len(tables), 23),
        ("Views", len(views), 5),
        ("Indexes", len(idx), len(INDEXES) or 14),
        ("RLS enabled", sum(t["relrowsecurity"] for t in tables), 23),
        ("SELECT policies", len(pol), 23),
    ]
    ok = all(a == b for _, a, b in checks) and len(INDEXES) == 14
    fk_ok = bool(fks) and all(f["convalidated"] for f in fks)
    for name, a, b in checks:
        print(f"{'✅' if a == b else '❌'} {name}: {a}/{b}")
    print(f"{'✅' if fk_ok else '❌'} FK constraints: {'all valid' if fk_ok else 'invalid or missing'} ({len(fks)})")
    return 0 if ok and fk_ok else 1


sys.exit(asyncio.run(main()))
