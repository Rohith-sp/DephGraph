"""Shared helper: asyncpg DSN from apps/api settings (reads .env.local)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
from app.config import settings  # noqa: E402

# SQLAlchemy URL -> plain libpq/asyncpg DSN
DSN = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")

# Fixed identity of the dev token (make_dev_token.py): seed_db makes it a member of every team.
DEV_USER_ID = "00000000-0000-0000-0000-000000000001"

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres", "db"}  # + compose service names


def refuse_non_local(dsn: str = DSN) -> None:
    """Demo seeds write fake data (seed_db also truncates): local DB only unless --force."""
    from urllib.parse import urlsplit

    host = urlsplit(dsn).hostname
    if "--force" not in sys.argv[1:] and host not in LOCAL_HOSTS:
        sys.exit(f"refusing to seed {host}: not a local database (pass --force to override)")
