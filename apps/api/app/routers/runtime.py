from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

from app.auth import (
    Member,
    authenticated_claims,
    bearer,
    current_member,
    get_current_user,
)
from app.database import DbSession
from app.schemas.common import Envelope, ok
from app.schemas.runtime import RuntimeSamplesRequest, RuntimeSamplesResult
from app.services import tokens
from app.services.runtime import record_samples

router = APIRouter(prefix="/runtime", tags=["runtime"])


async def runtime_caller(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: DbSession,
) -> Member | tokens.ServiceScope:
    """A user (Supabase JWT) or a collector (service token), never anonymous."""
    if creds is not None and creds.credentials.startswith(tokens.PREFIX):
        async with session.begin():
            scope = await tokens.authenticate(session, creds.credentials)
        if scope is None:
            raise HTTPException(401, "Invalid token")
        return scope
    claims = await get_current_user(await authenticated_claims(request, creds), session)
    return await current_member(claims, session)


@router.post("/samples", response_model=Envelope[RuntimeSamplesResult])
async def post_samples(
    body: RuntimeSamplesRequest,
    session: DbSession,
    caller: Annotated[Member | tokens.ServiceScope, Depends(runtime_caller)],
) -> dict[str, object]:
    """Runtime liveness batch: 404 unknown service/deployment, 409 service has no deployment."""
    if isinstance(caller, tokens.ServiceScope):
        if body.service_id != caller.service_id:
            raise HTTPException(403, "Token is not valid for this service")
        return ok(await record_samples(session, body, None))  # scope already checked
    return ok(await record_samples(session, body, caller))
