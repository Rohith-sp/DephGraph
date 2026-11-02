"""Copyleft contamination: packages carrying a copyleft license in a service's closure."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.queries import Rows, rows


async def copyleft_exposure(session: AsyncSession, service_id: int | None = None) -> Rows:
    return await rows(
        session,
        """
        SELECT service_id, service_name, package_name, version_string, spdx_id, depth
        FROM v_copyleft_risk
        WHERE CAST(:sid AS BIGINT) IS NULL OR service_id = CAST(:sid AS BIGINT)
        ORDER BY service_id, package_name
        """,
        sid=service_id,
    )
