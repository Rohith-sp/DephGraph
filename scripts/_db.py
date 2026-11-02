"""Shared helper: asyncpg DSN from apps/api settings (reads .env.local)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
from app.config import settings  # noqa: E402

# SQLAlchemy URL -> plain libpq/asyncpg DSN
DSN = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
