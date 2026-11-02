from typing import Annotated, Any

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings

bearer = HTTPBearer(auto_error=False)


def decode_token(token: str) -> dict[str, Any]:
    """Verify a Supabase access token (HS256, aud=authenticated); raises JWTError."""
    return jwt.decode(
        token, settings.supabase_jwt_secret, algorithms=["HS256"], audience="authenticated"
    )


async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict[str, Any]:
    """Return the verified claims of the bearer token."""
    if creds is None or not settings.supabase_jwt_secret:
        raise HTTPException(401, "Not authenticated")
    try:
        return decode_token(creds.credentials)
    except JWTError:
        raise HTTPException(401, "Invalid token") from None
