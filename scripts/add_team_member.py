"""Add a user to one or more teams (idempotent; updates the role if already a member).

  python scripts/add_team_member.py --team-slug platform payments --user-id <uuid> [--role admin]
  python scripts/add_team_member.py --team-slug data --email dev@example.com

--email is resolved through the Supabase Auth admin API (SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY).
"""

import argparse
import asyncio
import sys
import uuid

import asyncpg
import httpx
from _db import DSN, settings

from app.database import close_all
from app.services import cache


async def user_id_for_email(email: str) -> uuid.UUID:
    key = settings.supabase_service_role_key
    if not (settings.supabase_url and key):
        sys.exit(
            "--email needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY; use --user-id instead"
        )
    async with httpx.AsyncClient(base_url=settings.supabase_url, timeout=30) as c:
        for page in range(1, 100):
            r = await c.get(
                "/auth/v1/admin/users",
                params={"page": page, "per_page": 1000},
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
            )
            r.raise_for_status()
            users = r.json()["users"]
            for u in users:
                if (u.get("email") or "").lower() == email.lower():
                    return uuid.UUID(u["id"])
            if len(users) < 1000:
                break
    sys.exit(f"no Supabase user with email {email}")


async def add_member(
    conn: asyncpg.Connection, slugs: list[str], user_id: uuid.UUID, role: str = "member"
) -> None:
    teams = {
        r["slug"]: r["team_id"]
        for r in await conn.fetch(
            "SELECT team_id, slug FROM team WHERE slug = ANY($1)", slugs
        )
    }
    if missing := set(slugs) - teams.keys():
        sys.exit(f"unknown team slug(s): {', '.join(sorted(missing))}")
    await conn.executemany(
        "INSERT INTO team_member (team_id, user_id, role) VALUES ($1, $2, $3) "
        "ON CONFLICT (team_id, user_id) DO UPDATE SET role = EXCLUDED.role",
        [(teams[s], user_id, role) for s in slugs],
    )
    await cache.bump(memberships=True)  # the API caches memberships


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--team-slug", nargs="+", required=True)
    who = p.add_mutually_exclusive_group(required=True)
    who.add_argument("--user-id", type=uuid.UUID)
    who.add_argument("--email")
    p.add_argument("--role", choices=["member", "admin"], default="member")
    a = p.parse_args()
    user_id = a.user_id or await user_id_for_email(a.email)
    conn = await asyncpg.connect(DSN)
    try:
        await add_member(conn, a.team_slug, user_id, a.role)
    finally:
        await conn.close()
    await close_all()
    print(f"{user_id} is {a.role} of {', '.join(a.team_slug)}")


if __name__ == "__main__":
    asyncio.run(main())
