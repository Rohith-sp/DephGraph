"""Team authorization and invite claiming. Every function runs in the caller's transaction."""

import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Member
from app.services.queries import rows


async def require_admin(session: AsyncSession, member: Member, team_id: int) -> None:
    """404 for a team the caller is not in (indistinguishable from a missing one), 403 for non-admins.

    The role is read from the database, not the membership cache, and the caller's row is locked
    FOR SHARE until the transaction ends, so a concurrent demotion cannot slip in between this
    check and the write that relies on it. (Routes that edit members use `lock_admins` instead:
    taking SHARE and then UPDATE on the same row would deadlock.)
    """
    if team_id not in member.team_ids:
        raise HTTPException(404, "Team not found")
    found = await rows(
        session,
        "SELECT role FROM team_member WHERE team_id = :t AND user_id = :u FOR SHARE",
        t=team_id,
        u=member.user_id,
    )
    if not found or found[0]["role"] != "admin":
        raise HTTPException(403, "Team admin only")


async def lock_admins(session: AsyncSession, member: Member, team_id: int) -> set[uuid.UUID]:
    """The team's admins, row-locked until the transaction ends, after checking the caller is one.

    Two requests that each remove 'the other' last admin serialize on these locks, and the caller's
    own admin row is covered by the same lock.
    """
    if team_id not in member.team_ids:
        raise HTTPException(404, "Team not found")
    found = await rows(
        session,
        "SELECT user_id FROM team_member WHERE team_id = :t AND role = 'admin' "
        "ORDER BY user_id FOR UPDATE",
        t=team_id,
    )
    admins = {r["user_id"] for r in found}
    if member.user_id not in admins:
        raise HTTPException(403, "Team admin only")
    return admins


async def claim_invites(session: AsyncSession, user_id: uuid.UUID, email: str) -> list[int]:
    """Turn the live invites for a CONFIRMED `email` into memberships; returns the team ids.

    One statement: DELETE ... RETURNING feeds the INSERT, so concurrent claims cannot both win,
    and a user already in the team keeps their role. Expired invites are deleted, not honoured.
    """
    found = await rows(
        session,
        "WITH claimed AS (DELETE FROM team_invite WHERE email = :e "
        "RETURNING team_id, role, expires_at) "
        "INSERT INTO team_member (team_id, user_id, role, email) "
        "SELECT team_id, :u, role, :e FROM claimed WHERE expires_at > now() "
        "ON CONFLICT (team_id, user_id) DO NOTHING RETURNING team_id",
        e=email,
        u=user_id,
    )
    return [r["team_id"] for r in found]
