import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

from app.auth import CurrentMember, get_current_user, is_org_admin, verified_email
from app.database import DbSession
from app.schemas.common import BigId, Envelope, ok
from app.schemas.teams import (
    Done,
    InviteCreate,
    InviteOut,
    MemberOut,
    MemberPatch,
    TeamCreate,
    TeamOut,
)
from app.services import cache
from app.services.queries import rows
from app.services.teams import lock_admins, require_admin

router = APIRouter(prefix="/teams", dependencies=[Depends(get_current_user)], tags=["teams"])
LAST_ADMIN = "A team needs at least one admin"
MEMBER_COLUMNS = "user_id::text AS user_id, email, role, created_at"


@router.post("", response_model=Envelope[TeamOut], status_code=201)
async def create_team(
    body: TeamCreate,
    session: DbSession,
    member: CurrentMember,
    claims: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    """Organisation admins (ORG_ADMIN_EMAILS) create teams; the creator becomes the team's admin."""
    if not await is_org_admin(claims, session):
        raise HTTPException(403, "Only organisation admins can create teams")
    email = await verified_email(claims, session)
    try:
        async with session.begin():
            team = (
                await rows(
                    session,
                    "INSERT INTO team (name, slug) VALUES (:n, :s) RETURNING team_id, name, slug",
                    n=body.name,
                    s=body.slug,
                )
            )[0]
            await rows(
                session,
                "INSERT INTO team_member (team_id, user_id, role, email) "
                "VALUES (:t, :u, 'admin', :e) RETURNING team_id",
                t=team["team_id"],
                u=member.user_id,
                e=email,
            )
    except IntegrityError:
        raise HTTPException(409, "A team with that name or slug already exists") from None
    await cache.bump(memberships=True)
    return ok({**team, "role": "admin"})


@router.get("/{team_id}/members", response_model=Envelope[list[MemberOut]])
async def list_members(team_id: BigId, session: DbSession, member: CurrentMember) -> dict[str, Any]:
    if team_id not in member.team_ids:
        raise HTTPException(404, "Team not found")
    async with session.begin():
        found = await rows(
            session,
            f"SELECT {MEMBER_COLUMNS} FROM team_member WHERE team_id = :t "
            "ORDER BY created_at, user_id",
            t=team_id,
        )
    return ok(found)


@router.patch("/{team_id}/members/{user_id}", response_model=Envelope[MemberOut])
async def set_member_role(
    team_id: BigId, user_id: uuid.UUID, body: MemberPatch, session: DbSession, member: CurrentMember
) -> dict[str, Any]:
    async with session.begin():
        admins = await lock_admins(session, member, team_id)
        if body.role != "admin" and admins == {user_id}:
            raise HTTPException(409, LAST_ADMIN)
        found = await rows(
            session,
            "UPDATE team_member SET role = :r WHERE team_id = :t AND user_id = :u "
            f"RETURNING {MEMBER_COLUMNS}",
            r=body.role,
            t=team_id,
            u=user_id,
        )
    if not found:
        raise HTTPException(404, "Member not found")
    return ok(found[0])


@router.delete("/{team_id}/members/{user_id}", response_model=Envelope[Done])
async def remove_member(
    team_id: BigId, user_id: uuid.UUID, session: DbSession, member: CurrentMember
) -> dict[str, Any]:
    async with session.begin():
        if (await lock_admins(session, member, team_id)) == {user_id}:
            raise HTTPException(409, LAST_ADMIN)
        found = await rows(
            session,
            "DELETE FROM team_member WHERE team_id = :t AND user_id = :u RETURNING user_id",
            t=team_id,
            u=user_id,
        )
    if not found:
        raise HTTPException(404, "Member not found")
    await cache.bump(memberships=True)
    return ok({"ok": True})


@router.get("/{team_id}/invites", response_model=Envelope[list[InviteOut]])
async def list_invites(team_id: BigId, session: DbSession, member: CurrentMember) -> dict[str, Any]:
    async with session.begin():
        await require_admin(session, member, team_id)
        found = await rows(
            session,
            "SELECT email, role, created_at, expires_at FROM team_invite WHERE team_id = :t "
            "AND expires_at > now() ORDER BY created_at",
            t=team_id,
        )
    return ok(found)


@router.post("/{team_id}/invites", response_model=Envelope[InviteOut], status_code=201)
async def create_invite(
    team_id: BigId, body: InviteCreate, session: DbSession, member: CurrentMember
) -> dict[str, Any]:
    """Invite by email (no mail is sent): the person joins by signing in with that email, within 7 days."""
    try:
        async with session.begin():
            await require_admin(session, member, team_id)
            found = await rows(
                session,
                "INSERT INTO team_invite (team_id, email, role, invited_by) "
                "VALUES (:t, :e, :r, :u) ON CONFLICT (team_id, email) DO UPDATE SET "
                "role = EXCLUDED.role, invited_by = EXCLUDED.invited_by, "
                "created_at = now(), expires_at = now() + interval '7 days' "
                "RETURNING email, role, created_at, expires_at",
                t=team_id,
                e=body.email,
                r=body.role,
                u=member.user_id,
            )
    except IntegrityError:
        raise HTTPException(422, "Invalid invite") from None
    return ok(found[0])


@router.delete("/{team_id}/invites/{email}", response_model=Envelope[Done])
async def delete_invite(
    team_id: BigId, email: str, session: DbSession, member: CurrentMember
) -> dict[str, Any]:
    async with session.begin():
        await require_admin(session, member, team_id)
        found = await rows(
            session,
            "DELETE FROM team_invite WHERE team_id = :t AND email = :e RETURNING email",
            t=team_id,
            e=email.strip().lower(),
        )
    if not found:
        raise HTTPException(404, "Invite not found")
    return ok({"ok": True})
