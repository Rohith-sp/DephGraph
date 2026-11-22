"""Imports a service's manifests from GitHub as its production deployment.

Entry points: `import_default_branch` (service created) and `handle` (stored push / installation /
installation_repositories deliveries). Services are matched by GitHub's numeric repository id (the
normalised URL only while a service has none) and, when a service records its installation, only
that installation's events count. Each import claims (service, commit sha) in service_import, so
redeliveries and overlapping triggers ingest a commit once; a push whose commit is no longer the
default branch head is dropped, so an older delivery can never overwrite a newer import. Files come
from the repo at that sha through a contents:read token and go through the normal ingest path.
"""

from typing import Any

import httpx
import structlog
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal
from app.routers.ingest import ingest_many
from app.schemas.ingest import IngestRequest
from app.services import alerts, github, http
from app.services.manifest_parser import parse_manifest
from app.services.queries import REPO_URL_SQL, normalize_repo_url, rows

log = structlog.get_logger()
EVENTS = {"push", "installation", "installation_repositories"}
PERMISSIONS = {"contents": "read"}
COMMIT_CAP = 2048  # GitHub lists at most this many commits in a push payload
STALE_CLAIM_MINUTES = 10
DEV_MARKERS = ("dev", "test")  # requirements-dev.txt & co. are not production dependencies


def _ecosystem(name: str) -> str | None:
    """Ecosystem of a manifest, pyproject or lockfile by file name; None for anything else."""
    return github.ecosystem_of(name) or github.FULL_FILE_ECOSYSTEM.get(name.lower())


def _supported(name: str) -> bool:
    return _ecosystem(name) is not None and not any(m in name.lower() for m in DEV_MARKERS)


def wanted(event: str, payload: dict[str, Any]) -> bool:
    """Whether a delivery is worth storing; ValueError when malformed."""
    try:
        if event == "installation":
            return payload["action"] == "created"
        if event == "installation_repositories":
            return payload["action"] == "added"
        default = payload["repository"]["default_branch"]
        return payload["ref"] == f"refs/heads/{default}" and not payload.get("deleted")
    except (KeyError, TypeError) as e:
        raise ValueError(f"Malformed {event} payload") from e


async def _services(
    session: AsyncSession, repo_id: int | None, repo_url: str, installation_id: int | None
) -> list[dict[str, Any]]:
    async with session.begin():
        return await rows(
            session,
            "SELECT service_id, team_id, repo_url, root_path, github_installation_id FROM service "
            f"WHERE (github_repo_id = :rid OR (github_repo_id IS NULL AND {REPO_URL_SQL} = :url)) "
            "AND (github_installation_id IS NULL OR github_installation_id = :inst) "
            "ORDER BY service_id",
            rid=repo_id,
            url=normalize_repo_url(repo_url),
            inst=installation_id,
        )


def _touches(payload: dict[str, Any], root_path: str) -> bool:
    """Whether the push changed a supported manifest directly under root_path. A payload at
    GitHub's commit cap is incomplete, and a forced push or one without commit details
    (before != after) may have changed anything: all count as touching."""
    commits = payload.get("commits") or []
    if len(commits) >= COMMIT_CAP or payload.get("forced"):
        return True
    if not commits:
        return payload.get("before") != payload.get("after")
    root = root_path.strip("/")
    for c in commits:
        for path in (*c.get("added", ()), *c.get("modified", ()), *c.get("removed", ())):
            directory, _, name = path.rpartition("/")
            if directory == root and _supported(name):
                return True
    return False


async def _files(
    client: httpx.AsyncClient, repo: str, token: str, root_path: str, sha: str
) -> list[tuple[str, str, str]]:
    """(filename, ecosystem, content) of the supported manifests directly under root_path."""
    auth = {**github.HEADERS, "Authorization": f"Bearer {token}"}
    base = f"{github.API}/repos/{repo}/contents"
    r = await http.request(
        client,
        "GET",
        f"{base}/{root_path.strip('/')}".rstrip("/"),
        headers=auth,
        params={"ref": sha},
    )
    if r.status_code == 404:
        return []  # root_path does not exist at this commit
    r.raise_for_status()
    if not isinstance(listing := r.json(), list):
        return []  # root_path is a file
    found = []
    for entry in sorted(listing, key=lambda e: e["name"]):
        name = entry["name"]
        if entry["type"] != "file" or not _supported(name):
            continue
        raw = await http.request(
            client,
            "GET",
            f"{base}/{entry['path']}",
            headers={**auth, "Accept": "application/vnd.github.raw+json"},
            params={"ref": sha},
        )
        raw.raise_for_status()
        found.append((name, str(_ecosystem(name)), raw.text))
    return found


async def _head(client: httpx.AsyncClient, repo: str, token: str, branch: str) -> str:
    r = await http.request(
        client,
        "GET",
        f"{github.API}/repos/{repo}/git/ref/heads/{branch}",
        headers={**github.HEADERS, "Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    return str(r.json()["object"]["sha"])


async def _import_at(
    session: AsyncSession,
    client: httpx.AsyncClient,
    svc: dict[str, Any],
    repo: str,
    token: str,
    sha: str,
) -> dict[str, Any]:
    sid = svc["service_id"]
    async with session.begin():  # atomic claim; a claim older than STALE_CLAIM_MINUTES is retaken
        claimed = await rows(
            session,
            "INSERT INTO service_import (service_id, sha) VALUES (:s, :h) "
            "ON CONFLICT (service_id, sha) DO UPDATE SET started_at = now() "
            "WHERE service_import.status = 'importing' "
            "AND service_import.started_at < now() - make_interval(mins => :m) RETURNING 1 AS ok",
            s=sid,
            h=sha,
            m=STALE_CLAIM_MINUTES,
        )
    if not claimed:
        return {"status": "unchanged", "sha": sha, "manifests": 0, "team_id": svc["team_id"]}
    ingested = 0
    try:
        bodies = []
        for name, ecosystem, content in await _files(client, repo, token, svc["root_path"], sha):
            try:
                parse_manifest(ecosystem, content, name)
                bodies.append(
                    IngestRequest(
                        service_id=sid, ecosystem=ecosystem, content=content, filename=name  # type: ignore[arg-type]
                    )
                )
            except (ValueError, ValidationError):
                log.warning("import_manifest_skipped", service=sid, file=name, sha=sha)
        if bodies:  # every readable manifest becomes ONE deployment (a lockfile beats its manifest)
            try:
                await ingest_many(session, bodies)
                ingested = len(bodies)
            except HTTPException as e:
                if e.status_code != 422:
                    raise  # Mongo/DB trouble: let the delivery be retried
                log.warning("import_manifests_empty", service=sid, sha=sha)
        async with session.begin():
            await session.execute(
                text(
                    "UPDATE service_import SET status = 'done' WHERE service_id = :s AND sha = :h"
                ),
                {"s": sid, "h": sha},
            )
    except Exception:
        async with session.begin():  # release the claim so the retry starts at once
            await session.execute(
                text(
                    "DELETE FROM service_import WHERE service_id = :s AND sha = :h "
                    "AND status = 'importing'"
                ),
                {"s": sid, "h": sha},
            )
        raise
    return {"status": "imported", "sha": sha, "manifests": ingested, "team_id": svc["team_id"]}


async def _import_default(
    session: AsyncSession, svc: dict[str, Any], installation_id: int | None = None
) -> dict[str, Any]:
    repo = github.full_name(svc["repo_url"])
    if repo is None:
        return {"status": "no_repo"}
    async with httpx.AsyncClient(timeout=15) as client:
        token = await github.repo_token(
            client, repo, PERMISSIONS, svc["github_installation_id"] or installation_id
        )
        if token is None:
            return {"status": "not_installed"}
        info = await http.request(
            client,
            "GET",
            f"{github.API}/repos/{repo}",
            headers={**github.HEADERS, "Authorization": f"Bearer {token}"},
        )
        info.raise_for_status()
        head = await _head(client, repo, token, info.json()["default_branch"])
        return await _import_at(session, client, svc, repo, token, head)


async def import_default_branch(session: AsyncSession, service_id: int) -> dict[str, Any]:
    """Import the service's manifests from the head of its repo's default branch.

    `session` must have no open transaction (the service row must already be committed).
    Returns {"status": "imported" | "unchanged" | "not_installed" | "no_repo", ...}; "not_installed"
    means the GitHub App cannot read the repo: tell the user to install it. 404 for unknown ids.
    """
    async with session.begin():
        found = await rows(
            session,
            "SELECT service_id, team_id, repo_url, root_path, github_installation_id "
            "FROM service WHERE service_id = :s",
            s=service_id,
        )
    if not found:
        raise HTTPException(404, "Service not found")
    return await _import_default(session, found[0])


async def _push(session: AsyncSession, payload: dict[str, Any]) -> list[dict[str, Any]]:
    repo = payload["repository"]
    installation_id = (payload.get("installation") or {}).get("id")
    services = [
        s
        for s in await _services(session, repo.get("id"), repo["html_url"], installation_id)
        if _touches(payload, s["root_path"])
    ]
    results = []
    async with httpx.AsyncClient(timeout=15) as client:
        for svc in services:
            token = await github.repo_token(
                client,
                repo["full_name"],
                PERMISSIONS,
                svc["github_installation_id"] or installation_id,
            )
            if token is None:
                log.info("import_skipped_no_installation", repo=repo["full_name"])
                continue
            if (
                await _head(client, repo["full_name"], token, repo["default_branch"])
                != payload["after"]
            ):
                log.info("import_skipped_stale_push", repo=repo["full_name"], sha=payload["after"])
                results.append({"status": "stale", "team_id": svc["team_id"]})
                continue
            results.append(
                await _import_at(session, client, svc, repo["full_name"], token, payload["after"])
            )
    return results


async def handle(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Process a stored delivery (raises -> retried), then raise alerts for the teams it changed."""
    async with SessionLocal() as session:
        if event == "push":
            results = await _push(session, payload)
        else:  # installation (created) lists `repositories`; installation_repositories `..._added`
            installation_id = (payload.get("installation") or {}).get("id")
            added = payload.get(
                "repositories_added" if event == "installation_repositories" else "repositories"
            )
            results = [
                await _import_default(session, svc, installation_id)
                for repo in added or []
                for svc in await _services(
                    session,
                    repo.get("id"),
                    f"https://github.com/{repo['full_name']}",
                    installation_id,
                )
            ]
    teams = sorted({r["team_id"] for r in results if r["status"] == "imported"})
    if teams:
        await alerts.run(teams)
    return {"services": len(results), "imported": sum(r["status"] == "imported" for r in results)}
