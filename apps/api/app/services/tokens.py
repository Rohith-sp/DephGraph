"""Service ingest tokens: `dgs_<32 random bytes>`, only the SHA-256 is stored, scoped to one service."""

import hashlib
import secrets
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.queries import rows

PREFIX = "dgs_"


@dataclass(frozen=True)
class ServiceScope:
    """A caller authenticated by a service token: may act on this one service only."""

    service_id: int


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create(session: AsyncSession, service_id: int, name: str) -> dict[str, object]:
    """Store a new token (in the caller's transaction); the plaintext is returned once, never kept."""
    token = PREFIX + secrets.token_urlsafe(32)
    row = (
        await rows(
            session,
            "INSERT INTO service_token (service_id, token_hash, name) VALUES (:s, :h, :n) "
            "RETURNING token_id, name, created_at, last_used_at, revoked_at",
            s=service_id,
            h=_hash(token),
            n=name,
        )
    )[0]
    return {**row, "token": token}


async def authenticate(session: AsyncSession, token: str) -> ServiceScope | None:
    """The service of a live token (and mark it used), or None; runs in the caller's transaction."""
    found = await rows(
        session,
        "UPDATE service_token SET last_used_at = now() "
        "WHERE token_hash = :h AND revoked_at IS NULL RETURNING service_id",
        h=_hash(token),
    )
    return ServiceScope(found[0]["service_id"]) if found else None
