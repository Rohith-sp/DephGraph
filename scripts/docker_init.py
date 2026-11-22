"""Compose init: apply pending migrations, seed demo data only into an empty DB, then mint a dev JWT."""
import asyncio
import subprocess
import sys
from pathlib import Path

import asyncpg
import migrate
from _db import DSN
from seed_db import main as seed

TOKEN = Path("/shared/dev_token")


async def init() -> None:
    c = await asyncpg.connect(DSN)
    try:
        await migrate.migrate(c)
        empty = await c.fetchval("SELECT NOT EXISTS (SELECT 1 FROM service)")
    finally:
        await c.close()
    if empty:
        await seed(DSN)
    else:
        print("db already has data, skipping seed")


try:
    asyncio.run(init())
except migrate.MigrationError as e:
    sys.exit(f"migrate: {e}")
# Minted every `up` (7-day expiry); the web container reads it at start.
TOKEN.write_text(subprocess.check_output([sys.executable, str(Path(__file__).parent / "make_dev_token.py")], text=True).strip())
