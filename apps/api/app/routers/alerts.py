from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.auth import CurrentMember, get_current_user
from app.database import DbSession
from app.schemas.alerts import AlertSettingsOut, AlertSettingsPatch
from app.schemas.common import BigId, Envelope, ok
from app.services import cache
from app.services.queries import rows

router = APIRouter(prefix="/teams", dependencies=[Depends(get_current_user)], tags=["alerts"])


async def _settings(session: DbSession, team_id: int, member: CurrentMember) -> dict[str, Any]:
    """The team's alert settings with the caller's edit right; 404 for a non-member. The role is
    read fresh, not from the cached membership, so a demoted admin loses the right at once.
    """
    found = await rows(
        session,
        "SELECT t.alerts_enabled AS enabled, t.alert_min_severity AS min_severity, "
        "m.role = 'admin' AS can_edit FROM team t JOIN team_member m ON m.team_id = t.team_id "
        "WHERE t.team_id = :t AND m.user_id = :u",
        t=team_id,
        u=member.user_id,
    )
    if not found:
        raise HTTPException(404, "Team not found")
    return found[0]


@router.get("/{team_id}/alerts", response_model=Envelope[AlertSettingsOut])
async def get_alerts(team_id: BigId, session: DbSession, member: CurrentMember) -> dict[str, Any]:
    async with session.begin():
        return ok(await _settings(session, team_id, member))


@router.patch("/{team_id}/alerts", response_model=Envelope[AlertSettingsOut])
async def set_alerts(
    team_id: BigId, body: AlertSettingsPatch, session: DbSession, member: CurrentMember
) -> dict[str, Any]:
    """Team admin: turn GitHub Issue alerts on/off and set the minimum severity."""
    async with session.begin():
        if not (await _settings(session, team_id, member))["can_edit"]:
            raise HTTPException(403, "Team admin required")
        updated = await rows(
            session,
            "UPDATE team SET alerts_enabled = COALESCE(:e, alerts_enabled), "
            "alert_min_severity = COALESCE(:s, alert_min_severity) WHERE team_id = :t "
            "RETURNING alerts_enabled AS enabled, alert_min_severity AS min_severity",
            e=body.enabled,
            s=body.min_severity,
            t=team_id,
        )
    await cache.bump([team_id])
    return ok({**updated[0], "can_edit": True})
