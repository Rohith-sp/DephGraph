"""End-to-end demo against the running API with a real Supabase user token.

Prereqs: `supabase start`, uvicorn on :8000 (README "Run locally"). Rerunnable: it truncates and
reseeds the SQL tables first (local DB only; --force overrides). Live OSV / deps.dev / GitHub calls (no tokens needed).
"""

import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import httpx
import jwt
import seed_db
from add_team_member import add_member
from _db import DSN, settings

API = os.environ.get("API_URL", "http://localhost:8000")
SB = settings.supabase_url
FIXTURES = Path(__file__).resolve().parents[1] / "apps/api/tests/fixtures"
EMAIL, PASSWORD = "demo@dephgraph.local", "demo-password-1"
# (service, ecosystem, fixture)
INGEST = [
    ("api-gateway", "pypi", "requirements_flask.txt"),
    ("checkout-web", "npm", "package_react.json"),
    ("payments-worker", "cargo", "Cargo_tokio.toml"),
]
# Public PR that bumps one dependency in package.json; checkout-web is pointed at its repo.
PR_REPO, PR_NUMBER = "expressjs/express", 7580


def step(title: str) -> None:
    print(f"\n== {title}")


async def sign_in() -> str:
    """Create the demo user via the Auth admin API if missing, then password sign-in."""
    async with httpx.AsyncClient(base_url=SB, timeout=30) as c:
        key = settings.supabase_service_role_key
        await c.post(
            "/auth/v1/admin/users",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            json={"email": EMAIL, "password": PASSWORD, "email_confirm": True},
        )  # 422 "already registered" on reruns is fine
        r = await c.post(
            "/auth/v1/token?grant_type=password",
            headers={"apikey": settings.supabase_anon_key},
            json={"email": EMAIL, "password": PASSWORD},
        )
        r.raise_for_status()
        return r.json()["access_token"]


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres", "db"}  # + docker-compose service names


def reset_allowed(dsn: str, force: bool) -> bool:
    """seed_db TRUNCATEs every table: only on a local DB unless --force."""
    return force or urlsplit(dsn).hostname in LOCAL_HOSTS


async def main(force: bool = False) -> None:
    if not reset_allowed(DSN, force):
        sys.exit(
            f"refusing to TRUNCATE {urlsplit(DSN).hostname}: not a local database "
            "(pass --force to wipe it anyway)"
        )
    step(f"Reset + seed ({DSN.rsplit('@', 1)[1]})")
    await seed_db.main()
    token = await sign_in()
    print(f"signed in as {EMAIL} (token alg header via {SB})")
    conn = await asyncpg.connect(DSN)  # seeding wiped team_member: the demo user joins every team
    sub = jwt.decode(token, options={"verify_signature": False})["sub"]
    await add_member(conn, ["platform", "payments", "data"], uuid.UUID(sub), "admin")
    await conn.close()
    async with httpx.AsyncClient(
        base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=180
    ) as c:

        async def get(path: str, **params):
            r = await c.get(path, params=params)
            r.raise_for_status()
            return r.json()["data"]

        svc = {s["name"]: s for s in await get("/services")}

        step("Ingest manifests (live OSV + deps.dev)")
        for name, eco, fixture in INGEST:
            r = await c.post(
                "/ingest/manifest",
                json={
                    "service_id": svc[name]["service_id"],
                    "ecosystem": eco,
                    "filename": fixture,
                    "content": (FIXTURES / fixture).read_text(),
                },
            )
            r.raise_for_status()
            d = r.json()["data"]
            print(
                f"{name:16} {fixture:22} parsed={d['packages_parsed']} vulns={d['vulns_fetched']}"
                f" {d['duration_ms']}ms"
            )

        step("Blast radius: api-gateway")
        br = await get(f"/blast-radius/{svc['api-gateway']['service_id']}")
        print(f"{br['total_vulns']} vulns, {br['critical_count']} critical")
        for p in br["vulnerable_packages"][:5]:
            ids = ", ".join(v["osv_id"] for v in p["vulns"][:2])
            print(f"  {p['name']} {p['version']}  path={' > '.join(p['path'])}  {ids}")

        step("Remediation plan: team 1 (compute, persist, mark first action DONE)")
        plan = await get("/remediation/plan/1")
        for s in plan[:5]:
            print(
                f"  {s['step']}. {s['package']} {s['from_version']} -> {s['to_version']}"
                f"  fixes {len(s['vulns_fixed'])}  cumulative {s['cumulative_coverage_pct']}%"
            )
        r = await c.post("/remediation/plan", json={"team_id": 1, "title": "Demo plan"})
        r.raise_for_status()
        saved = r.json()["data"]
        r = await c.patch(
            f"/remediation/plan/{saved['plan_id']}/actions/1", json={"status": "DONE"}
        )
        r.raise_for_status()
        print(
            f"saved plan {saved['plan_id']} ({len(saved['actions'])} actions, "
            f"coverage {saved['coverage_pct']}%); action 1 -> {r.json()['data']['status']}"
        )

        step("Top critical vulnerabilities")
        top = await get(
            "/vulnerabilities", severity="CRITICAL", sort="cvss_score", order="desc", page_size=5
        )
        for v in top["items"]:
            print(
                f"  {v['osv_id']:22} cvss={v['cvss_score']} services={v['affected_services']}"
                f" fix={v['has_fix']}  {v['title'][:50]}"
            )
        print(f"  ({top['total']} critical in total)")

        step("Stats overview")
        print(" ", json.dumps(await get("/stats/overview")))

        step("Cluster recompute (SBERT + UMAP + HDBSCAN)")
        before = {c_["cluster_id"] for c_ in await get("/clusters")}
        r = await c.post("/clusters/recompute")
        r.raise_for_status()
        t0 = time.monotonic()
        while time.monotonic() - t0 < 180:
            await asyncio.sleep(2)
            clusters = await get("/clusters")
            if clusters and {c_["cluster_id"] for c_ in clusters} != before:
                break
        else:
            raise SystemExit("cluster recompute did not finish in 180s")
        print(f"{len(clusters)} clusters in {time.monotonic() - t0:.0f}s")
        for cl in clusters[:6]:
            print(
                f"  {cl['label']:28} members={cl['member_count']} {', '.join(cl['keywords'][:4])}"
            )
        red = await get("/clusters/redundancy")
        print(f"redundancy: {len(red)} (service, cluster) pairs with overlapping packages")
        for x in red[:3]:
            print(f"  {x['service_name']}: {x['cluster_label']} -> {', '.join(x['packages'])}")

        step(f"PR gate: signed pull_request webhook for {PR_REPO}#{PR_NUMBER}")
        repo_url = f"https://github.com/{PR_REPO}"
        conn = await asyncpg.connect(DSN)
        await conn.execute("UPDATE service SET repo_url = $1 WHERE name = 'checkout-web'", repo_url)
        await conn.close()
        body = json.dumps(
            {
                "action": "opened",
                "pull_request": {
                    "number": PR_NUMBER,
                    "head": {"sha": "0" * 40},
                    "title": "bump hbs",
                    "user": {"login": "demo"},
                    "html_url": f"{repo_url}/pull/{PR_NUMBER}",
                },
                "repository": {"full_name": PR_REPO, "html_url": repo_url},
            }
        ).encode()
        sig = hmac.new(settings.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        r = await c.post(
            "/pr-gate/webhook",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": f"sha256={sig}",
                "Content-Type": "application/json",
            },
        )
        if r.status_code >= 400:
            print(
                f"  webhook -> {r.status_code} {r.text[:200]} (GitHub unauthenticated rate "
                "limit? retry later)"
            )
        else:
            print(f"webhook -> {r.status_code} {r.json()['data']} (review runs in the background)")
            sid = svc["checkout-web"]["service_id"]
            deadline = time.monotonic() + 90
            while True:
                mine = [v for v in await get(f"/pr-gate/verdicts/{sid}") if v["pr_number"] == PR_NUMBER]
                if mine or time.monotonic() > deadline:
                    break
                await asyncio.sleep(2)
            if not mine:
                print("  no verdict after 90s (GitHub rate limit? check the API log)")
            for v in mine:
                print(f"  {v['dep_name']}: {v['verdict']} (stage {v['stage_reached']}) - {v['reasoning']}")


if __name__ == "__main__":
    asyncio.run(main(force="--force" in sys.argv[1:]))
