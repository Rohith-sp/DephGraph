"""Additive, idempotent demo data on top of scripts/seed_db.py (never truncates).

Adds: ~50 described packages in 6 functional groups (for semantic clustering), maintainer
last_active_at + maintained_by spread, 4 services whose deployments resolve several packages of the
same group (redundancy), and a 520-node `monolith-demo` service for dashboard stress tests.
Every insert is ON CONFLICT DO NOTHING / deterministic UPDATE, so running it twice changes nothing.
Requires migration 007 (maintainer.last_active_at).
"""

import asyncio
import hashlib
import random
from datetime import datetime, timedelta, timezone

import asyncpg
from _db import DSN

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
N_DEMO = 520

# (ecosystem, name, description). Descriptions share a group phrase so embeddings cluster.
GROUPS: dict[str, list[tuple[str, str, str]]] = {
    "date-time": [
        ("pypi", "pendulum", "Date and time library: timezone aware datetime manipulation and formatting"),
        ("pypi", "arrow", "Date and time library: human friendly dates, times and timestamps"),
        ("pypi", "python-dateutil", "Date and time library: powerful extensions to the standard datetime module"),
        ("pypi", "pytz", "Date and time library: world timezone definitions and timezone conversion"),
        ("npm", "dayjs", "Date and time library: minimalist parsing, formatting and manipulating of dates"),
        ("npm", "date-fns", "Date and time library: modern utility functions for dates and timestamps"),
        ("npm", "luxon", "Date and time library: wrapper for dates, times, durations and timezones"),
    ],
    "testing": [
        ("pypi", "pytest", "Test framework: simple powerful unit testing with fixtures and assertions"),
        ("pypi", "hypothesis", "Test framework: property based testing that generates test cases"),
        ("pypi", "nose2", "Test framework: unit test runner extending the standard unittest"),
        ("pypi", "pytest-mock", "Test framework plugin: mocking and stubbing for unit tests"),
        ("npm", "jest", "Test framework: JavaScript unit testing with mocks and assertions"),
        ("npm", "mocha", "Test framework: feature rich JavaScript test runner for unit tests"),
        ("npm", "vitest", "Test framework: fast unit test runner with mocks and assertions"),
        ("npm", "jasmine", "Test framework: behavior driven JavaScript unit testing"),
        ("npm", "tape", "Test framework: tap producing unit test harness for node"),
    ],
    "logging": [
        ("pypi", "loguru", "Logging library: simple structured log messages and log file rotation"),
        ("pypi", "structlog", "Logging library: structured log entries with processors and context"),
        ("pypi", "colorlog", "Logging library: colored log output for the terminal log handlers"),
        ("npm", "winston", "Logging library: multi transport log messages with levels and formats"),
        ("npm", "pino", "Logging library: very low overhead structured JSON log messages"),
        ("npm", "bunyan", "Logging library: simple structured log messages with log levels"),
        ("npm", "log4js", "Logging library: log messages with appenders and log levels"),
    ],
    "serialization": [
        ("pypi", "orjson", "Data serialization library: fast JSON serializer and deserializer"),
        ("pypi", "ujson", "Data serialization library: ultra fast JSON encoder and decoder"),
        ("pypi", "simplejson", "Data serialization library: JSON encoder and decoder for data"),
        ("pypi", "ruamel.yaml", "Data serialization library: YAML parser and emitter preserving comments"),
        ("pypi", "toml", "Data serialization library: TOML configuration parser and writer"),
        ("npm", "js-yaml", "Data serialization library: YAML parser and dumper for configuration data"),
        ("npm", "yaml", "Data serialization library: YAML parser and stringifier for data documents"),
        ("npm", "fast-json-stringify", "Data serialization library: fast JSON serializer using schemas"),
    ],
    "cli": [
        ("pypi", "typer", "Command line argument parser: build CLI apps with type hints, options and flags"),
        ("pypi", "docopt", "Command line argument parser: CLI interface description language for options"),
        ("pypi", "fire", "Command line argument parser: generate CLI commands from any python object"),
        ("npm", "commander", "Command line argument parser: complete CLI solution with options and commands"),
        ("npm", "yargs", "Command line argument parser: build interactive CLI tools with options and flags"),
        ("npm", "meow", "Command line argument parser: helper for CLI apps with flags and help text"),
    ],
    "database": [
        ("pypi", "psycopg2", "Database driver: PostgreSQL adapter for connecting and querying SQL databases"),
        ("pypi", "asyncpg", "Database driver: fast asynchronous PostgreSQL client for SQL databases"),
        ("pypi", "pymongo", "Database driver: MongoDB client for connecting and querying document databases"),
        ("pypi", "peewee", "Database ORM: small object relational mapper for SQL databases"),
        ("pypi", "tortoise-orm", "Database ORM: asynchronous object relational mapper for SQL databases"),
        ("npm", "sequelize", "Database ORM: promise based object relational mapper for SQL databases"),
        ("npm", "typeorm", "Database ORM: object relational mapper for TypeScript and SQL databases"),
        ("npm", "knex", "Database query builder: SQL queries and migrations for SQL databases"),
        ("npm", "mongoose", "Database ODM: object modeling for MongoDB document databases"),
        ("npm", "pg", "Database driver: PostgreSQL client for connecting and querying SQL databases"),
    ],
}
ALL = [(e, n, d, g) for g, items in GROUPS.items() for e, n, d in items]

# (name, language, team_id, [packages]) -> each resolves 2+ packages of the same group directly
NEW_SERVICES = [
    ("billing-api", "python", 2, ["pendulum", "arrow", "python-dateutil", "pytest", "nose2", "loguru", "orjson", "ujson", "ruamel.yaml"]),
    ("reporting-web", "typescript", 3, ["dayjs", "date-fns", "luxon", "jest", "mocha", "winston", "pino", "yaml", "js-yaml"]),
    ("notifier-svc", "python", 1, ["structlog", "colorlog", "loguru", "typer", "docopt", "fire", "psycopg2", "asyncpg", "peewee"]),
    ("admin-cli", "typescript", 3, ["commander", "yargs", "meow", "sequelize", "typeorm", "knex", "vitest", "tape"]),
]

# seed_db's six maintainers (by username, not id) -> days since last activity
EXISTING_ACTIVITY = {
    "alice-dev": 5, "bob-oss": 20, "carol-rs": 45, "dave-py": 400, "erin-js": 900, "frank-core": 1400,
}
# New maintainers. username -> days idle. "idle-*" are dedicated single-maintainer packages.
ACTIVE_POOL = {f"active-maint-{i}": d for i, d in enumerate([3, 9, 14, 30, 60, 100, 150, 200], 1)}
IDLE_POOL = {f"idle-maint-{i}": d for i, d in enumerate([800, 1000, 1300, 1700, 2200, 3000], 1)}


def h(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest(), 16)


def ts(days_ago: int) -> datetime:
    return NOW - timedelta(days=days_ago)


async def main(dsn: str = DSN) -> None:
    c = await asyncpg.connect(dsn)
    async with c.transaction():
        # --- 1. packages + versions -------------------------------------------------------------
        await c.executemany(
            "INSERT INTO package (ecosystem, name, description) VALUES ($1, $2, $3) "
            "ON CONFLICT (ecosystem, name) DO NOTHING",
            [(e, n, d) for e, n, d, _ in ALL],
        )
        lic = {r[0]: r[1] for r in await c.fetch("SELECT spdx_id, license_id FROM license")}
        lic_ids = [lic[k] for k in ("MIT", "Apache-2.0") if k in lic] or [None]
        pid = {
            (r["ecosystem"], r["name"]): r["package_id"]
            for r in await c.fetch("SELECT package_id, ecosystem, name FROM package")
        }
        vers: dict[str, list[str]] = {}
        rows = []
        for e, n, _, _ in ALL:
            hv = h(n)
            major = hv % 4
            vs = [f"{major}.{m}.{(hv >> (4 * m)) % 10}" for m in range(1 + hv % 2)]
            vers[n] = vs
            for i, v in enumerate(vs):
                rows.append((pid[(e, n)], v, lic_ids[hv % len(lic_ids)], ts(100 + hv % 1500 + (len(vs) - i) * 90)))
        await c.executemany(
            "INSERT INTO package_version (package_id, version_string, license_id, published_at) "
            "VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
            rows,
        )

        # --- 2. maintainers ---------------------------------------------------------------------
        pool = {**ACTIVE_POOL, **IDLE_POOL}
        await c.executemany(
            "INSERT INTO maintainer (username, profile_url) VALUES ($1, $2) "
            "ON CONFLICT (platform, username) DO NOTHING",
            [(u, f"https://github.com/{u}") for u in pool],
        )
        for u, days in {**EXISTING_ACTIVITY, **pool}.items():
            await c.execute("UPDATE maintainer SET last_active_at = $2 WHERE username = $1", u, ts(days))
        mid_of = {r["username"]: r["maintainer_id"] for r in await c.fetch("SELECT maintainer_id, username FROM maintainer")}
        active, idle = list(ACTIVE_POOL), list(IDLE_POOL)
        mb = []
        for k, (e, n, _, _) in enumerate(ALL):
            if k % 6 == 0:  # single maintainer idle > 2 years (or ~1-2y) -> high abandonment
                owners = [idle[(k // 6) % len(idle)]]
            elif k % 6 == 1:  # single maintainer, active
                owners = [active[k % len(active)]]
            else:  # multi-maintainer, active
                owners = [active[(k + j) % len(active)] for j in range(2 + k % 2)]
            mb += [(pid[(e, n)], mid_of[o], "owner" if j == 0 else "maintainer") for j, o in enumerate(owners)]
        await c.executemany(
            "INSERT INTO maintained_by (package_id, maintainer_id, role) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            mb,
        )

        # --- 4. services resolving several packages from one functional group -------------------
        by_name = {n: (e, n) for e, n, _, _ in ALL}
        for name, lang, team, pkgs in NEW_SERVICES:
            await c.execute(
                "INSERT INTO service (team_id, name, repo_url, language) VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (team_id, name) DO NOTHING",
                team, name, f"https://github.com/acme/{name}", lang,
            )
            sid = await c.fetchval("SELECT service_id FROM service WHERE team_id=$1 AND name=$2", team, name)
            await c.execute(
                "INSERT INTO deployment (service_id, deploy_seq, environment, deployed_at, "
                "manifest_ecosystems) VALUES ($1, 1, 'production', $2, $3) ON CONFLICT DO NOTHING",
                sid, ts(10), sorted({by_name[p][0] for p in pkgs}),
            )
            await c.executemany(
                "INSERT INTO resolves_to (service_id, deploy_seq, package_id, version_string, depth, is_direct) "
                "VALUES ($1, 1, $2, $3, 0, TRUE) ON CONFLICT DO NOTHING",
                [(sid, pid[by_name[p]], vers[p][0]) for p in pkgs],
            )

        # --- 3. monolith-demo: N_DEMO synthetic package-versions in one deployment ------------------
        await c.executemany(
            "INSERT INTO package (ecosystem, name, description) VALUES ('npm', $1, '') "
            "ON CONFLICT (ecosystem, name) DO NOTHING",
            [(f"demo-lib-{i:03d}",) for i in range(1, N_DEMO + 1)],
        )
        demo = {
            r["name"]: r["package_id"]
            for r in await c.fetch("SELECT package_id, name FROM package WHERE ecosystem='npm' AND name LIKE 'demo-lib-%'")
        }
        node = {i: demo[f"demo-lib-{i:03d}"] for i in range(1, N_DEMO + 1)}
        await c.executemany(
            "INSERT INTO package_version (package_id, version_string, license_id, published_at) "
            "VALUES ($1, '1.0.0', $2, $3) ON CONFLICT DO NOTHING",
            [(node[i], lic_ids[i % len(lic_ids)], ts(30 + i)) for i in range(1, N_DEMO + 1)],
        )
        # DAG: binary tree (i -> 2i, 2i+1) plus a few extra forward edges; edges only point to larger i.
        rng = random.Random(7)
        edges: set[tuple[int, int]] = set()
        for i in range(1, N_DEMO + 1):
            for j in (2 * i, 2 * i + 1):
                if j <= N_DEMO:
                    edges.add((i, j))
            if i < N_DEMO - 1 and rng.random() < 0.3:
                edges.add((i, rng.randint(i + 1, N_DEMO)))
        await c.executemany(
            "INSERT INTO depends_on VALUES ($1, '1.0.0', $2, '1.0.0', 'runtime') ON CONFLICT DO NOTHING",
            [(node[a], node[b]) for a, b in sorted(edges)],
        )
        await c.execute(
            "INSERT INTO service (team_id, name, repo_url, language) VALUES (1, 'monolith-demo', "
            "'https://github.com/acme/monolith-demo', 'typescript') ON CONFLICT (team_id, name) DO NOTHING"
        )
        sid = await c.fetchval("SELECT service_id FROM service WHERE team_id=1 AND name='monolith-demo'")
        await c.execute(
            "INSERT INTO deployment (service_id, deploy_seq, environment, deployed_at, "
            "manifest_ecosystems) VALUES ($1, 1, 'production', $2, ARRAY['npm']) "
            "ON CONFLICT DO NOTHING",
            sid, ts(5),
        )
        # BFS min depth from direct deps (nodes 1..8)
        adj: dict[int, list[int]] = {}
        for a, b in edges:
            adj.setdefault(a, []).append(b)
        depth = {i: 0 for i in range(1, 9)}
        frontier = list(depth)
        while frontier:
            nxt = []
            for a in frontier:
                for b in adj.get(a, []):
                    if b not in depth:
                        depth[b] = depth[a] + 1
                        nxt.append(b)
            frontier = nxt
        await c.executemany(
            "INSERT INTO resolves_to VALUES ($1, 1, $2, '1.0.0', $3, $4) ON CONFLICT DO NOTHING",
            [(sid, node[i], d, d == 0) for i, d in sorted(depth.items())],
        )
    n_pkg = await c.fetchval("SELECT count(*) FROM package")
    n_rt = await c.fetchval("SELECT count(*) FROM resolves_to WHERE service_id = $1", sid)
    print(f"packages total={n_pkg} monolith service_id={sid} resolves_to={n_rt} maintainers="
          f"{await c.fetchval('SELECT count(*) FROM maintainer')} maintained_by="
          f"{await c.fetchval('SELECT count(*) FROM maintained_by')}")
    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
