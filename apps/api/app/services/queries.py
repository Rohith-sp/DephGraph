"""Shared read queries (parameterized text SQL against the latest deployment per service)."""

from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

LATEST = (
    "latest AS (SELECT DISTINCT ON (service_id) service_id, deploy_seq "
    "FROM deployment ORDER BY service_id, deploy_seq DESC)"
)

Rows = list[dict[str, Any]]


async def rows(session: AsyncSession, sql: str, **params: Any) -> Rows:
    return [dict(r) for r in (await session.execute(text(sql), params)).mappings()]


async def require_service(session: AsyncSession, service_id: int) -> dict[str, Any]:
    found = await rows(
        session, "SELECT service_id, name FROM service WHERE service_id = :id", id=service_id
    )
    if not found:
        raise HTTPException(404, "Service not found")
    return found[0]


async def lock_service(session: AsyncSession, service_id: int) -> None:
    """Serialize writers per service until the transaction ends (no row lock held on `service`)."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:s)"), {"s": service_id})


async def latest_seq(session: AsyncSession, service_id: int) -> int | None:
    r = await rows(
        session,
        "SELECT max(deploy_seq) AS seq FROM deployment WHERE service_id = :id",
        id=service_id,
    )
    return r[0]["seq"]


async def open_vuln_rows(session: AsyncSession, team_id: int | None = None) -> Rows:
    """One row per (service, vulnerable package version, vuln) in latest deployments, minus waivers."""
    return await rows(
        session,
        f"""
        WITH {LATEST}
        SELECT s.service_id, s.name AS service_name, p.package_id, p.name AS package_name,
               p.ecosystem, a.version_string, v.vuln_id, v.osv_id, v.severity,
               v.cvss_score, a.fixed_in
        FROM latest l
        JOIN service s ON s.service_id = l.service_id
        JOIN resolves_to r ON (r.service_id, r.deploy_seq) = (l.service_id, l.deploy_seq)
        JOIN affects a ON (a.package_id, a.version_string) = (r.package_id, r.version_string)
        JOIN vulnerability v ON v.vuln_id = a.vuln_id
        JOIN package p ON p.package_id = a.package_id
        WHERE (CAST(:team_id AS BIGINT) IS NULL OR s.team_id = CAST(:team_id AS BIGINT))
          AND NOT EXISTS (
            SELECT 1 FROM waived_for w
            WHERE w.vuln_id = v.vuln_id AND w.service_id = s.service_id
              AND (w.expires_on IS NULL OR w.expires_on >= CURRENT_DATE))
        ORDER BY s.service_id, p.name, v.vuln_id
        """,
        team_id=team_id,
    )


async def prunable_rows(session: AsyncSession, service_id: int | None = None) -> Rows:
    """Packages observed in the latest deployment but never live (v_prunable semantics)."""
    return await rows(
        session,
        f"""
        WITH {LATEST}
        SELECT p.package_id, p.name, p.ecosystem, r.version_string AS version,
               COALESCE(max(o.exec_count), 0) AS exec_count, r.deploy_seq
        FROM latest l
        JOIN resolves_to r ON (r.service_id, r.deploy_seq) = (l.service_id, l.deploy_seq)
        JOIN package p ON p.package_id = r.package_id
        JOIN observes o ON (o.service_id, o.deploy_seq, o.package_id, o.version_string)
                         = (r.service_id, r.deploy_seq, r.package_id, r.version_string)
        WHERE (CAST(:sid AS BIGINT) IS NULL OR r.service_id = CAST(:sid AS BIGINT))
        GROUP BY p.package_id, p.name, p.ecosystem, r.version_string, r.deploy_seq
        HAVING NOT bool_or(o.is_live)
        ORDER BY p.name
        """,
        sid=service_id,
    )
