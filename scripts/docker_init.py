"""Compose init (local dev only): apply migrations, seed demo data into an empty DB when
DEPHGRAPH_SEED_DEMO=1 (compose sets it), then mint a dev JWT. Needs ./scripts mounted: the image has no seed/token scripts."""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import asyncpg
import migrate
from _db import DSN

TOKEN = Path("/shared/dev_token")


async def init() -> None:
    c = await asyncpg.connect(DSN)
    try:
        await migrate.migrate(c)
        empty = await c.fetchval("SELECT NOT EXISTS (SELECT 1 FROM service)")
    finally:
        await c.close()
    if empty and os.environ.get("DEPHGRAPH_SEED_DEMO") == "1":
        from seed_db import main as seed

        await seed(DSN)
    else:
        print("skipping demo seed (db has data or DEPHGRAPH_SEED_DEMO != 1)")


try:
    asyncio.run(init())
except migrate.MigrationError as e:
    sys.exit(f"migrate: {e}")
# Minted every `up` (7-day expiry); the web container reads it at start.
TOKEN.write_text(subprocess.check_output([sys.executable, str(Path(__file__).parent / "make_dev_token.py")], text=True).strip())
