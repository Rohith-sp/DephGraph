from fastapi import APIRouter, Depends

from app.auth import CurrentMember, get_current_user
from app.database import DbSession
from app.schemas.common import Envelope, ok
from app.schemas.runtime import RuntimeSamplesRequest, RuntimeSamplesResult
from app.services.runtime import record_samples

router = APIRouter(prefix="/runtime", dependencies=[Depends(get_current_user)], tags=["runtime"])


@router.post("/samples", response_model=Envelope[RuntimeSamplesResult])
async def post_samples(
    body: RuntimeSamplesRequest, session: DbSession, member: CurrentMember
) -> dict[str, object]:
    """Runtime liveness batch: 404 unknown service/deployment, 409 service has no deployment."""
    return ok(await record_samples(session, body, member))
