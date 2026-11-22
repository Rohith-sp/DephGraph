"""Check the live database against what the migration files declare: tables, columns, views,
indexes, RLS, policies, private tables, valid FKs and migration history.

Nothing here is a hand-kept list or count: the expected schema is parsed from apps/api/migrations/*.sql
(CREATE TABLE / ALTER TABLE ADD|DROP COLUMN / CREATE VIEW / CREATE|DROP INDEX / REVOKE), so a new
migration is checked the moment it exists. Private tables (API only) are the ones a migration REVOKEs
from the Supabase roles; they must have RLS but no policy and no SELECT grant for `authenticated`.
"""
import asyncio
import re
import sys
from pathlib import Path

import asyncpg
import migrate
from _db import DSN

MIGRATION_DIR = Path(__file__).parents[1] / "apps/api/migrations"
TABLE_CONSTRAINT = {"PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "CONSTRAINT", "EXCLUDE", "LIKE"}


def _top_level(body: str) -> list[str]:
    """Split a CREATE TABLE body on the commas that are not inside parentheses."""
    parts, depth, cur = [], 0, ""
    for ch in body:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    return parts + [cur]


def expected(sql: str | None = None) -> dict:
    """The schema the migrations declare: tables -> columns, views, indexes, private tables."""
    if sql is None:
        sql = "\n".join(p.read_text() for p in sorted(MIGRATION_DIR.glob("*.sql")))
    sql = re.sub(r"--[^\n]*", "", sql)
    columns: dict[str, set[str]] = {}
    for m in re.finditer(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)\s*\(", sql):
        depth, i = 1, m.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(sql[i], 0)
            i += 1
        first = [re.match(r"\w+", part.strip()) for part in _top_level(sql[m.end() : i - 1])]
        columns[m[1]] = {w[0] for w in first if w and w[0].upper() not in TABLE_CONSTRAINT}
    for m in re.finditer(r"ALTER TABLE (\w+)([^;]*);", sql):
        if m[1] in columns:
            columns[m[1]] |= set(re.findall(r"ADD COLUMN (?:IF NOT EXISTS )?(\w+)", m[2]))
            columns[m[1]] -= set(re.findall(r"DROP COLUMN (?:IF EXISTS )?(\w+)", m[2]))
    dropped = set(re.findall(r"DROP INDEX (?:IF EXISTS )?(\w+)", sql))
    created = set(re.findall(r"CREATE (?:UNIQUE )?INDEX (?:IF NOT EXISTS )?(\w+)", sql))
    private = {
        t.strip()
        for m in re.finditer(r"REVOKE \w+ ON ([\w, ]+?) FROM", sql)
        for t in m[1].split(",")
        if t.strip() in columns
    }
    return {
        "columns": columns,
        "views": set(re.findall(r"CREATE (?:OR REPLACE )?VIEW (\w+)", sql)),
        "indexes": created - dropped,
        "dropped": dropped,
        "private": private,
    }


async def check(c: asyncpg.Connection) -> int:
    want = expected()
    tables = {t: set() for t in want["columns"]}
    have_tables = await c.fetch(
        "SELECT c.relname, c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> 'schema_migrations'"  # the runner's ledger
    )
    rls = {r["relname"]: r["relrowsecurity"] for r in have_tables}
    for r in await c.fetch("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'"):
        if r["table_name"] in tables:
            tables[r["table_name"]].add(r["column_name"])
    views = {r[0] for r in await c.fetch("SELECT viewname FROM pg_views WHERE schemaname = 'public'")}
    indexes = {r[0] for r in await c.fetch("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")}
    with_policy = {r[0] for r in await c.fetch("SELECT DISTINCT tablename FROM pg_policies WHERE schemaname = 'public' AND cmd = 'SELECT'")}
    readable = {
        r[0]
        for r in await c.fetch(
            "SELECT relname FROM pg_class WHERE relname = ANY($1) AND has_table_privilege('authenticated', oid, 'SELECT')",
            sorted(want["private"]),
        )
    }
    fks = await c.fetch("SELECT conname, convalidated FROM pg_constraint WHERE contype = 'f'")
    try:
        pending = [p.name for _, p, _ in (await migrate.state(c))[1]]
    except migrate.MigrationError as e:  # an applied file was edited
        pending = [str(e)]

    names = set(want["columns"])
    public = names - want["private"]
    column_drift = {
        t: sorted(want["columns"][t] ^ tables[t]) for t in names & set(rls) if want["columns"][t] != tables[t]
    }
    problems = {  # check -> (what was found wrong); empty = fine
        f"Tables ({len(names)} in migrations)": sorted(names ^ set(rls)),
        f"Columns of every table ({sum(map(len, want['columns'].values()))})": [f"{t}: {', '.join(d)}" for t, d in sorted(column_drift.items())],
        f"Views ({len(want['views'])})": sorted(want["views"] - views),
        f"Indexes ({len(want['indexes'])})": sorted(want["indexes"] - indexes),
        "Dropped indexes still present": sorted(want["dropped"] & indexes),
        f"RLS enabled ({len(names)})": sorted(t for t in names if not rls.get(t)),
        f"SELECT policies on the {len(public)} non-private tables": sorted(public - with_policy),
        f"Private tables ({len(want['private'])}) without policy or grant": sorted((want["private"] & with_policy) | readable),
        "Migrations pending or changed": pending,
    }
    fk_bad = [f["conname"] for f in fks if not f["convalidated"]]
    problems[f"FK constraints ({len(fks)}) valid"] = fk_bad or ([] if fks else ["none found"])
    for name, bad in problems.items():
        print(f"{'❌' if bad else '✅'} {name}" + (f": {', '.join(bad)}" if bad else ""))
    return int(any(problems.values()))


async def main() -> int:
    c = await asyncpg.connect(DSN)
    try:
        return await check(c)
    finally:
        await c.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
