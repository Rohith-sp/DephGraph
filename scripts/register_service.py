"""Register a service for a repository so the PR gate reviews that repo's pull requests.

  python scripts/register_service.py --repo https://github.com/Rohith-sp/DephGraph --team platform
  python scripts/register_service.py --repo <url> --team data --name my-service --language python

Idempotent: re-running for the same team and name updates the repo URL and language. The name
defaults to the repository name. The team must exist (see seed_db.py / add_team_member.py).
"""

import argparse
import asyncio
import sys

import asyncpg
from _db import DSN

from app.database import close_all
from app.services import cache


async def register(
    conn: asyncpg.Connection, repo: str, team_slug: str, name: str | None, language: str | None
) -> tuple[int, str]:
    """(service_id, name); exits when the team does not exist."""
    team_id = await conn.fetchval("SELECT team_id FROM team WHERE slug = $1", team_slug)
    if team_id is None:
        sys.exit(f"unknown team slug: {team_slug}")
    name = name or repo.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]
    service_id = await conn.fetchval(
        "INSERT INTO service (team_id, name, repo_url, language) VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (team_id, name) DO UPDATE SET repo_url = EXCLUDED.repo_url, "
        "language = COALESCE(EXCLUDED.language, service.language) RETURNING service_id",
        team_id,
        name,
        repo,
        language,
    )
    await cache.bump([team_id])
    return service_id, name


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", required=True, help="repository URL, e.g. https://github.com/org/repo")
    p.add_argument("--team", required=True, help="team slug")
    p.add_argument("--name")
    p.add_argument("--language")
    a = p.parse_args()
    conn = await asyncpg.connect(DSN)
    try:
        service_id, name = await register(conn, a.repo, a.team, a.name, a.language)
    finally:
        await conn.close()
    await close_all()
    print(f"service {service_id} ({name}) registered for {a.repo}")


if __name__ == "__main__":
    asyncio.run(main())
