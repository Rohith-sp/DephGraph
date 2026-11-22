from typing import Any

from fastapi import APIRouter, Depends

from app.auth import CurrentMember, get_current_user
from app.database import DbSession
from app.schemas.analysis import MeOut
from app.schemas.common import Envelope, ok
from app.services.queries import rows

router = APIRouter(dependencies=[Depends(get_current_user)], tags=["me"])


@router.get("/me", response_model=Envelope[MeOut])
async def me(session: DbSession, member: CurrentMember) -> dict[str, Any]:
    """The caller and their teams (an empty list for a user nobody has added to a team yet)."""
    teams = await rows(
        session,
        "SELECT t.team_id, t.name, t.slug, m.role FROM team_member m "
        "JOIN team t ON t.team_id = m.team_id WHERE m.user_id = :u ORDER BY t.team_id",
        u=member.user_id,
    )
    return ok({"user_id": str(member.user_id), "teams": teams})
