from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.auth import (
    authenticated_claims,
    domain_allowed,
    is_org_admin,
    user_id_of,
    verified_email,
)
from app.database import DbSession
from app.schemas.analysis import MeOut
from app.schemas.common import Envelope, ok
from app.services import cache
from app.services.queries import rows
from app.services.teams import claim_invites

# Not behind get_current_user: a refused user gets {"allowed": false, "reason"}, not a 403.
router = APIRouter(tags=["me"])


@router.get("/me", response_model=Envelope[MeOut])
async def me(
    session: DbSession, claims: Annotated[dict[str, Any], Depends(authenticated_claims)]
) -> dict[str, Any]:
    """The caller and their teams (empty until an admin invites them).

    Live invites for the caller's confirmed email are claimed here, so signing in is enough.
    Access is by invitation: outside the allowed email domains, or with no team and not an
    organisation admin, `allowed` is false and every other route answers 403.
    """
    user_id = user_id_of(claims)
    out: dict[str, Any] = {
        "user_id": str(user_id),
        "allowed": False,
        "org_admin": False,
        "teams": [],
    }
    if not await domain_allowed(claims, session):
        return ok({**out, "reason": "Your email domain is not allowed"})
    email = await verified_email(claims, session)
    async with session.begin():
        claimed = await claim_invites(session, user_id, email) if email else []
        teams = await rows(
            session,
            "SELECT t.team_id, t.name, t.slug, m.role FROM team_member m "
            "JOIN team t ON t.team_id = m.team_id WHERE m.user_id = :u ORDER BY t.team_id",
            u=user_id,
        )
    if claimed:
        await cache.bump(memberships=True)
    org_admin = await is_org_admin(claims, session)
    if not teams and not org_admin:
        return ok({**out, "reason": "Access is by invitation: ask an admin to invite you"})
    return ok({**out, "allowed": True, "org_admin": org_admin, "teams": teams, "reason": None})
