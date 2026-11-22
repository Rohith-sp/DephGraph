"""Starts the clustering job. Python on Vercel cannot run minutes of work after the response, so
the API only dispatches the `cluster.yml` GitHub Actions workflow (app/jobs/cluster.py runs there).
"""

import httpx
from fastapi import HTTPException

from app.config import settings
from app.database import get_redis
from app.services import github, http

LOCK_KEY = "dephgraph:lock:cluster_recompute"  # held by the job while it runs
WORKFLOW = "cluster.yml"
REF = "main"


async def trigger() -> str:
    """ "already_running" when the job holds the lock, else "dispatched"; 503 when not configured."""
    if await get_redis().exists(LOCK_KEY):
        return "already_running"
    if not (settings.github_dispatch_token and settings.github_repository):
        raise HTTPException(
            503,
            "Clustering runs as a scheduled job; trigger it via GitHub Actions "
            "or `python -m app.jobs.cluster` locally",
        )
    async with httpx.AsyncClient(timeout=15) as client:
        r = await http.request(
            client,
            "POST",
            f"{github.API}/repos/{settings.github_repository}/actions/workflows/{WORKFLOW}/dispatches",
            headers={
                **github.HEADERS,
                "Authorization": f"Bearer {settings.github_dispatch_token}",
            },
            json={"ref": REF},
        )
        r.raise_for_status()  # 204 on success; 401/403/404 -> 502 via the httpx handler
    return "dispatched"
