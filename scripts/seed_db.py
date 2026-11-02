"""Idempotent, deterministic seed: truncates every table, then reloads demo data."""

import asyncio
import random
from datetime import datetime, timedelta, timezone

import asyncpg
from _db import DSN

rng = random.Random(42)
NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)

# Ordered low-level -> high-level so depends_on only points "down" (acyclic by construction).
PKGS = {
    "pypi": "markupsafe certifi idna urllib3 charset-normalizer click pyyaml numpy cryptography pillow "
    "jinja2 werkzeug requests httpx aiohttp pydantic sqlalchemy flask pandas mysql-connector-python",
    "npm": "ms inherits safe-buffer debug lodash minimist semver qs chalk uuid moment async "
    "follow-redirects node-fetch axios got express react webpack handlebars",
    "cargo": "libc cfg-if bytes serde regex rand serde_json tokio hyper reqwest",
}
PKGS = {e: v.split() for e, v in PKGS.items()}
CLUSTERS = {
    "HTTP client": "requests httpx aiohttp axios node-fetch got reqwest",
    "Serialization": "pyyaml serde serde_json",
}
GPL = {"mysql-connector-python", "handlebars", "regex"}  # synthetic license assignment for demo
ALIASES = {"pillow": ["PIL"], "pyyaml": ["yaml"], "beautifulsoup4": ["bs4"]}
SERVICES = [  # (team idx, name, language, ecosystem)
    (0, "api-gateway", "python", "pypi"),
    (0, "auth-service", "python", "pypi"),
    (1, "checkout-web", "typescript", "npm"),
    (1, "payments-worker", "rust", "cargo"),
    (2, "analytics-dashboard", "typescript", "npm"),
]
# (osv_id, severity, cvss, ecosystem) -- 3 CRITICAL, 5 HIGH, 4 MEDIUM, 3 LOW
VULNS = [
    (s, c, e)
    for s, c, e in [
        ("CRITICAL", 9.8, "pypi"),
        ("CRITICAL", 9.1, "npm"),
        ("CRITICAL", 10.0, "cargo"),
        ("HIGH", 8.1, "pypi"),
        ("HIGH", 7.5, "pypi"),
        ("HIGH", 8.8, "npm"),
        ("HIGH", 7.2, "npm"),
        ("HIGH", 7.5, "cargo"),
        ("MEDIUM", 6.1, "pypi"),
        ("MEDIUM", 5.9, "npm"),
        ("MEDIUM", 5.3, "npm"),
        ("MEDIUM", 4.8, "pypi"),
        ("LOW", 3.7, "pypi"),
        ("LOW", 3.1, "npm"),
        ("LOW", 2.4, "cargo"),
    ]
]


def ts(days_ago: int) -> datetime:
    return NOW - timedelta(days=days_ago)


async def main(dsn: str = DSN) -> None:
    rng.seed(42)  # deterministic on every call, not just the first
    c = await asyncpg.connect(dsn)
    async with c.transaction():
        names = [
            r[0]
            for r in await c.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        ]
        await c.execute(f"TRUNCATE {', '.join(names)} RESTART IDENTITY CASCADE")

        # Inserts rely on RESTART IDENTITY: BIGSERIAL ids are 1..n in insertion order.
        await c.executemany(
            "INSERT INTO team (name, slug) VALUES ($1, $2)",
            [("Platform", "platform"), ("Payments", "payments"), ("Data", "data")],
        )
        await c.executemany(
            "INSERT INTO service (team_id, name, repo_url, language) VALUES ($1, $2, $3, $4)",
            [(t + 1, n, f"https://github.com/acme/{n}", lang) for t, n, lang, _ in SERVICES],
        )
        await c.executemany(
            "INSERT INTO license (spdx_id, name, is_copyleft) VALUES ($1, $2, $3)",
            [
                ("MIT", "MIT License", False),
                ("Apache-2.0", "Apache License 2.0", False),
                ("GPL-3.0", "GNU General Public License v3.0", True),
            ],
        )
        await c.executemany(
            "INSERT INTO semantic_cluster (label, member_count) VALUES ($1, $2)",
            [(k, len(v.split())) for k, v in CLUSTERS.items()],
        )

        pkgs = [(e, n) for e, ns in PKGS.items() for n in ns]  # package_id = index + 1
        pid = {n: i + 1 for i, (_, n) in enumerate(pkgs)}
        await c.executemany(
            "INSERT INTO package (ecosystem, name, description) VALUES ($1, $2, $3)",
            [(e, n, f"{n} ({e} package)") for e, n in pkgs],
        )
        await c.executemany(
            "INSERT INTO package_alias VALUES ($1, $2)",
            [(pid[n], a) for n, al in ALIASES.items() if n in pid for a in al],
        )

        # 4 versions per package -> 200
        vers: dict[str, list[tuple[str, int]]] = {}
        rows = []
        for e, n in pkgs:
            major = rng.randint(0, 4)
            age = rng.choice([200, 400, 900, 1500, 2500])  # days since newest release
            lic = 3 if n in GPL else rng.choice([1, 2])
            cl = next((i + 1 for i, v in enumerate(CLUSTERS.values()) if n in v.split()), None)
            vs = [f"{major}.{m}.{rng.randint(0, 9)}" for m in range(4)]
            vers[n] = [(v, i) for i, v in enumerate(vs)]
            for i, v in enumerate(vs):
                rows.append(
                    (pid[n], v, lic, cl, ts(age + (3 - i) * 120), n == "node-fetch" and i == 0)
                )
        await c.executemany("INSERT INTO package_version VALUES ($1, $2, $3, $4, $5, $6)", rows)

        # maintainers: a few packages are unmaintained (abandonment view gets a spread)
        await c.executemany(
            "INSERT INTO maintainer (username, profile_url) VALUES ($1, $2)",
            [
                (u, f"https://github.com/{u}")
                for u in ["alice-dev", "bob-oss", "carol-rs", "dave-py", "erin-js", "frank-core"]
            ],
        )
        await c.executemany(
            "INSERT INTO maintainer_email VALUES ($1, $2, $3)",
            [
                (i, f"{u}@example.org", True)
                for i, u in enumerate(
                    ["alice-dev", "bob-oss", "carol-rs", "dave-py", "erin-js", "frank-core"], 1
                )
            ],
        )
        await c.executemany(
            "INSERT INTO maintained_by VALUES ($1, $2, $3)",
            [
                (pid[n], m, "owner")
                for e, n in pkgs
                if n not in ("async", "moment", "mysql-connector-python", "handlebars")
                for m in rng.sample(range(1, 7), 1 if rng.random() < 0.5 else 2)
            ],
        )

        # vulnerabilities + affects (2-3 versions of one package each; some without a fix)
        await c.executemany(
            "INSERT INTO vulnerability (osv_id, severity, cvss_score, title, published_at, modified_at) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            [
                (
                    f"GHSA-seed-{i:04d}",
                    s,
                    cv,
                    f"Seeded {s.lower()} advisory #{i} ({e})",
                    ts(300 - i * 10),
                    ts(100 - i),
                )
                for i, (s, cv, e) in enumerate(VULNS, 1)
            ],
        )
        affects = []
        for i, (_, _, e) in enumerate(VULNS, 1):
            n = rng.choice(PKGS[e][len(PKGS[e]) // 3 :])
            for v, idx in rng.sample(vers[n], rng.choice([2, 3])):
                fixed = None if i % 5 == 0 else f"{v.split('.')[0]}.9.9"
                affects.append((pid[n], v, i, fixed))
        affects = list({(a[0], a[1], a[2]): a for a in affects}.values())
        await c.executemany("INSERT INTO affects VALUES ($1, $2, $3, $4)", affects)

        # depends_on: each version depends on 0-3 versions of lower-level packages (same ecosystem)
        deps: dict[tuple[int, str], list[tuple[int, str]]] = {}
        dep_rows = []
        for e, ns in PKGS.items():
            for i, n in enumerate(ns):
                for v, _ in vers[n]:
                    if i == 0:
                        continue
                    for t in rng.sample(ns[:i], min(i, rng.randint(1, 3))):
                        tv = rng.choice(vers[t])[0]
                        scope = rng.choices(["runtime", "dev", "optional"], [8, 1, 1])[0]
                        deps.setdefault((pid[n], v), []).append((pid[t], tv))
                        dep_rows.append((pid[n], v, pid[t], tv, scope))
        await c.executemany("INSERT INTO depends_on VALUES ($1, $2, $3, $4, $5)", dep_rows)

        # deployments (service 1 has two), scans, and the materialized closure (BFS, min depth)
        deployments = [(s, 1) for s in range(1, 6)] + [(1, 2)]
        await c.executemany(
            "INSERT INTO deployment (service_id, deploy_seq, environment, deployed_at) "
            "VALUES ($1, $2, $3, $4)",
            [(s, q, "production" if q == 1 else "staging", ts(30 - q)) for s, q in deployments],
        )
        await c.executemany(
            "INSERT INTO scan_run (service_id, started_at, completed_at, source) VALUES ($1, $2, $3, $4)",
            [(s, ts(7), ts(7) + timedelta(minutes=5), "scheduled") for s, _ in deployments],
        )
        affected = {(a[0], a[1]) for a in affects}
        closure_rows, observes = [], []
        for scan_id, (s, q) in enumerate(deployments, 1):
            eco = SERVICES[s - 1][3]
            top = PKGS[eco][len(PKGS[eco]) // 2 :]
            direct = {pid[n]: rng.choice(vers[n])[0] for n in rng.sample(top, 5)}
            for n in CLUSTERS["HTTP client"].split():  # guarantee redundancy rows
                if n in PKGS[eco][:]:
                    direct[pid[n]] = rng.choice(vers[n])[0]
            bad = [
                k for k in sorted(affected) if pkgs[k[0] - 1][0] == eco
            ]  # one vulnerable direct dep per service
            direct.__setitem__(*bad[(s + q) % len(bad)])
            depth: dict[tuple[int, str], int] = {(p, v): 0 for p, v in direct.items()}
            frontier = list(depth)
            while frontier:
                nxt = []
                for node in frontier:
                    for d in deps.get(node, []):
                        if d not in depth:
                            depth[d] = depth[node] + 1
                            nxt.append(d)
                frontier = nxt
            for (p, v), d in depth.items():
                closure_rows.append((s, q, p, v, d, d == 0))
                live = rng.random() < 0.6
                observes.append((s, q, scan_id, p, v, rng.randint(1, 5000) if live else 0, live))
        await c.executemany("INSERT INTO resolves_to VALUES ($1, $2, $3, $4, $5, $6)", closure_rows)
        await c.executemany("INSERT INTO observes VALUES ($1, $2, $3, $4, $5, $6, $7)", observes)

        # small samples for the remaining tables
        await c.execute(
            "INSERT INTO waived_for VALUES (4, 1, 'Not reachable in gateway', $1)",
            (NOW + timedelta(days=90)).date(),
        )
        await c.execute(
            "INSERT INTO remediation_plan (team_id, title, coverage_pct) VALUES (1, 'Q4 critical fixes', 60)"
        )
        await c.executemany(
            "INSERT INTO remediation_action VALUES (1, $1, $2, 'PENDING')",
            [(1, "Upgrade vulnerable pypi packages"), (2, "Remove unused packages")],
        )
        await c.executemany("INSERT INTO resolves VALUES (1, $1, $2)", [(1, 1), (1, 4), (2, 9)])
        await c.execute(
            "INSERT INTO pr_review VALUES (1, 42, 'https://github.com/acme/api-gateway/pull/42', 'alice-dev', $1)",
            ts(2),
        )
        await c.executemany(
            "INSERT INTO pr_risk_verdict (service_id, pr_number, dep_name, risk_level, stage_reached, context_tokens, decided_at) "
            "VALUES (1, 42, $1, $2, $3, $4, $5)",
            [
                ("requests", "PASS", 1, None, ts(2)),
                ("mysql-connector-python", "BLOCK", 2, 1200, ts(2)),
            ],
        )

    n = {
        t: await c.fetchval(f"SELECT count(*) FROM {t}")
        for t in (
            "team",
            "service",
            "package",
            "package_version",
            "vulnerability",
            "affects",
            "resolves_to",
        )
    }
    await c.close()
    print(
        f"✅ {n['team']} teams, {n['service']} services, {n['package']} packages, "
        f"{n['package_version']} versions, {n['vulnerability']} vulns "
        f"({n['affects']} affects, {n['resolves_to']} resolves_to)"
    )


if __name__ == "__main__":
    asyncio.run(main())
