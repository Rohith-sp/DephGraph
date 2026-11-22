import json
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.auth import CurrentMember, get_current_user
from app.database import DbSession
from app.schemas.analysis import GraphOut, PrunableOut, ServiceOut
from app.schemas.common import BigId, Envelope, ok
from app.schemas.teams import (
    Done,
    ServiceBrief,
    ServiceCreate,
    ServicePatch,
    TokenCreate,
    TokenCreated,
    TokenOut,
)
from app.services import cache, github, github_import, tokens
from app.services.blast_radius import EDGES_SQL, NODES_SQL
from app.services.cache import cached_json
from app.services.queries import (
    LATEST,
    PRUNABLE_SQL,
    json_rows,
    latest_seq,
    require_service,
    rows,
    team_params,
)
from app.services.teams import require_admin

log = structlog.get_logger()
router = APIRouter(prefix="/services", dependencies=[Depends(get_current_user)], tags=["services"])


@router.get("", response_model=Envelope[list[ServiceOut]])
async def list_services(request: Request, session: DbSession, member: CurrentMember) -> Response:
    return await cached_json(
        request,
        lambda: json_rows(
            session,
            f"""
            WITH {LATEST},
            sv AS (
              SELECT l.service_id, r.package_id, r.version_string, v.vuln_id, v.severity
              FROM latest l
              JOIN resolves_to r ON (r.service_id, r.deploy_seq) = (l.service_id, l.deploy_seq)
              LEFT JOIN affects a ON (a.package_id, a.version_string)
                                   = (r.package_id, r.version_string)
                                 AND NOT EXISTS (
                                   SELECT 1 FROM waived_for w
                                   WHERE w.vuln_id = a.vuln_id AND w.service_id = l.service_id
                                     AND (w.expires_on IS NULL OR w.expires_on >= CURRENT_DATE))
              LEFT JOIN vulnerability v ON v.vuln_id = a.vuln_id)
            SELECT s.service_id, s.team_id, t.name AS team_name, s.name, s.language, s.repo_url, s.root_path,
                   COUNT(DISTINCT (sv.package_id, sv.version_string))
                     FILTER (WHERE sv.package_id IS NOT NULL) AS package_count,
                   COUNT(DISTINCT sv.vuln_id) AS vuln_count,
                   COUNT(DISTINCT sv.vuln_id) FILTER (WHERE sv.severity = 'CRITICAL')
                     AS critical_count
            FROM service s
            JOIN team t ON t.team_id = s.team_id
            LEFT JOIN sv ON sv.service_id = s.service_id
            WHERE s.team_id = ANY(:teams)
            GROUP BY s.service_id, t.name
            ORDER BY s.service_id
            """,
            **team_params(member),
        ),
        member,
    )


@router.get("/{service_id}/graph", response_model=Envelope[GraphOut])
async def service_graph(
    service_id: BigId, request: Request, session: DbSession, member: CurrentMember
) -> Response:
    async def build() -> str:
        svc = await require_service(session, service_id, member)
        seq = await latest_seq(session, service_id)
        params = {"s": service_id, "q": seq}
        nodes = edges = "[]"
        if seq is not None:
            nodes = await json_rows(
                session,
                f"SELECT package_id || '@' || version AS id, n.* FROM ({NODES_SQL}) n",
                **params,
            )
            edges = await json_rows(
                session,
                "SELECT dep_package_id || '@' || dep_version_string || '->' || ref_package_id "
                "|| '@' || ref_version_string AS id, "
                "dep_package_id || '@' || dep_version_string AS source, "
                f"ref_package_id || '@' || ref_version_string AS target FROM ({EDGES_SQL}) e",
                **params,
            )
        service = {"service_id": svc["service_id"], "name": svc["name"]}
        return f'{{"service":{json.dumps(service)},"nodes":{nodes},"edges":{edges}}}'

    return await cached_json(request, build, member)


@router.get("/{service_id}/prunable", response_model=Envelope[list[PrunableOut]])
async def service_prunable(
    service_id: BigId, request: Request, session: DbSession, member: CurrentMember
) -> Response:
    async def build() -> str:
        await require_service(session, service_id, member)
        return await json_rows(
            session,
            "SELECT package_id, name, ecosystem, version, exec_count, deploy_seq "
            f"FROM ({PRUNABLE_SQL}) p",
            sid=service_id,
            **team_params(member),
        )

    return await cached_json(request, build, member)


SERVICE_COLUMNS = "service_id, team_id, name, repo_url, root_path"
DUPLICATE = "This repository and directory is already registered, or the name is taken"


async def _verify_repo(repo_url: str) -> tuple[int | None, int | None]:
    """(installation id, repository id) of the GitHub App's access to the repository.

    Registering a repository means receiving its PR data, so only repositories the App is installed
    on (i.e. ones an owner granted access to) may be registered. Without a configured App (dev, CI)
    there is nothing to check and both ids stay NULL.
    """
    if not github.configured():
        return None, None
    full_name = repo_url.removeprefix("https://github.com/")
    async with httpx.AsyncClient(timeout=15) as client:
        access = await github.repo_access(client, full_name)
    if access is None:
        raise HTTPException(422, f"Install the DephGraph GitHub App on {full_name} first")
    return access


@router.post("", response_model=Envelope[ServiceBrief], status_code=201)
async def create_service(
    body: ServiceCreate, session: DbSession, member: CurrentMember
) -> dict[str, Any]:
    """Register a repository (or a monorepo subdirectory of it) as a service; team admins only."""
    async with session.begin():
        await require_admin(session, member, body.team_id)
    installation_id, repo_id = await _verify_repo(body.repo_url)
    repo = body.repo_url.rsplit("/", 1)[1]
    name = body.name or (f"{repo}/{body.root_path}" if body.root_path else repo)
    try:
        async with session.begin():
            await require_admin(session, member, body.team_id)
            created = (
                await rows(
                    session,
                    "INSERT INTO service (team_id, name, repo_url, root_path, "
                    "github_installation_id, github_repo_id) VALUES (:t, :n, :r, :p, :i, :g) "
                    f"RETURNING {SERVICE_COLUMNS}",
                    t=body.team_id,
                    n=name[:120],
                    r=body.repo_url,
                    p=body.root_path,
                    i=installation_id,
                    g=repo_id,
                )
            )[0]
    except IntegrityError:
        raise HTTPException(409, DUPLICATE) from None
    await cache.bump([body.team_id])
    return ok({**created, "import_status": await _import(session, created["service_id"])})


async def _import(session: DbSession, service_id: int) -> str | None:
    """Import the service's default branch; the outcome, never an error: registering the service
    must succeed even when GitHub or the raw store is down (a later push or reinstall retries)."""
    if not github.configured():
        return None
    try:
        return str((await github_import.import_default_branch(session, service_id))["status"])
    except Exception:  # noqa: BLE001 -- see docstring
        log.exception("initial_import_failed", service=service_id)
        return "failed"


async def _admin_service(session: DbSession, service_id: int, member: CurrentMember) -> int:
    """team_id of the service once the caller is confirmed admin of its team."""
    team_id = (await require_service(session, service_id, member))["team_id"]
    await require_admin(session, member, team_id)
    return team_id


@router.patch("/{service_id}", response_model=Envelope[ServiceBrief])
async def update_service(
    service_id: BigId, body: ServicePatch, session: DbSession, member: CurrentMember
) -> dict[str, Any]:
    changes = body.model_dump(exclude_none=True)
    moved = "repo_url" in changes or "root_path" in changes
    async with session.begin():
        await _admin_service(session, service_id, member)
    if "repo_url" in changes:  # a new repository is verified like a new registration
        changes["github_installation_id"], changes["github_repo_id"] = await _verify_repo(
            changes["repo_url"]
        )
    assignments = ", ".join(
        f"{col} = :{col}" for col in changes
    )  # column names are ours, not input
    try:
        async with session.begin():
            team_id = await _admin_service(session, service_id, member)
            updated = await rows(
                session,
                f"UPDATE service SET {assignments} WHERE service_id = :id "
                f"RETURNING {SERVICE_COLUMNS}",
                id=service_id,
                **changes,
            )
            if moved:  # other files, other manifests: the head commit must import again
                await session.execute(
                    text("DELETE FROM service_import WHERE service_id = :id"), {"id": service_id}
                )
    except IntegrityError:
        raise HTTPException(409, DUPLICATE) from None
    await cache.bump([team_id])
    return ok(
        {**updated[0], "import_status": await _import(session, service_id) if moved else None}
    )


@router.delete("/{service_id}", response_model=Envelope[Done])
async def delete_service(
    service_id: BigId, session: DbSession, member: CurrentMember
) -> dict[str, Any]:
    async with session.begin():
        team_id = await _admin_service(session, service_id, member)
        await rows(
            session, "DELETE FROM service WHERE service_id = :id RETURNING 1 AS x", id=service_id
        )
    await cache.bump([team_id])
    return ok({"ok": True})


@router.get("/{service_id}/tokens", response_model=Envelope[list[TokenOut]])
async def list_tokens(
    service_id: BigId, session: DbSession, member: CurrentMember
) -> dict[str, Any]:
    async with session.begin():
        await _admin_service(session, service_id, member)
        found = await rows(
            session,
            "SELECT token_id, name, created_at, last_used_at, revoked_at FROM service_token "
            "WHERE service_id = :s ORDER BY token_id",
            s=service_id,
        )
    return ok(found)


@router.post("/{service_id}/tokens", response_model=Envelope[TokenCreated], status_code=201)
async def create_token(
    service_id: BigId, body: TokenCreate, session: DbSession, member: CurrentMember
) -> dict[str, Any]:
    """Mint an ingest token for runtime collectors; the secret is in this response only."""
    async with session.begin():
        await _admin_service(session, service_id, member)
        created = await tokens.create(session, service_id, body.name)
    return ok(created)


@router.delete("/{service_id}/tokens/{token_id}", response_model=Envelope[Done])
async def revoke_token(
    service_id: BigId, token_id: BigId, session: DbSession, member: CurrentMember
) -> dict[str, Any]:
    async with session.begin():
        await _admin_service(session, service_id, member)
        found = await rows(
            session,
            "UPDATE service_token SET revoked_at = coalesce(revoked_at, now()) "
            "WHERE token_id = :t AND service_id = :s RETURNING token_id",
            t=token_id,
            s=service_id,
        )
    if not found:
        raise HTTPException(404, "Token not found")
    return ok({"ok": True})
