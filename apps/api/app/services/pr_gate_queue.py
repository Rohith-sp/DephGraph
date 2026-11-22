"""Durable PR-gate deliveries (table pr_gate_delivery, migration 011).

A delivery is stored before it is reviewed, so GitHub redeliveries are no-ops and a failed or
crashed review is retried by the sweeper. States: `processing` (claimed; next_attempt_at is its
lease), `pending` (failed, waiting for its backoff), `done`, `failed` (attempts exhausted).
"""

import json
from typing import Any

from sqlalchemy import text

from app.database import SessionLocal
from app.services.queries import rows

MAX_ATTEMPTS = 5
LEASE_MINUTES = 10  # longer than any review (Vercel maxDuration is 5 min)
RETENTION_DAYS = 30


async def record(delivery_id: str, event: str, payload: dict[str, Any]) -> bool:
    """Store a new delivery already claimed by the caller; False when it was seen before."""
    async with SessionLocal() as s, s.begin():
        return bool(
            await rows(
                s,
                "INSERT INTO pr_gate_delivery (delivery_id, event, payload, status, attempts, "
                "next_attempt_at) VALUES (:id, :event, CAST(:payload AS JSONB), 'processing', 1, "
                "now() + make_interval(mins => :lease)) "
                "ON CONFLICT (delivery_id) DO NOTHING RETURNING 1 AS new",
                id=delivery_id,
                event=event,
                payload=json.dumps(payload),
                lease=LEASE_MINUTES,
            )
        )


async def claim() -> tuple[str, dict[str, Any]] | None:
    """Claim one due delivery (new attempt, fresh lease), or None. SKIP LOCKED: concurrent
    sweepers never get the same row."""
    async with SessionLocal() as s, s.begin():
        found = await rows(
            s,
            "UPDATE pr_gate_delivery SET status = 'processing', attempts = attempts + 1, "
            "next_attempt_at = now() + make_interval(mins => :lease), updated_at = now() "
            "WHERE delivery_id = (SELECT delivery_id FROM pr_gate_delivery "
            "WHERE status IN ('pending', 'processing') AND attempts < :max "
            "AND next_attempt_at <= now() ORDER BY next_attempt_at LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "RETURNING delivery_id, payload",
            lease=LEASE_MINUTES,
            max=MAX_ATTEMPTS,
        )
    return (found[0]["delivery_id"], found[0]["payload"]) if found else None


async def done(delivery_id: str) -> None:
    async with SessionLocal() as s, s.begin():
        await s.execute(
            text(
                "UPDATE pr_gate_delivery SET status = 'done', last_error = NULL, "
                "updated_at = now() WHERE delivery_id = :id"
            ),
            {"id": delivery_id},
        )


async def fail(delivery_id: str, error: Exception) -> None:
    """Back off 2^attempts minutes, or give up (`failed`) once the attempts are used up."""
    async with SessionLocal() as s, s.begin():
        await s.execute(
            text(
                "UPDATE pr_gate_delivery SET last_error = :err, updated_at = now(), "
                "status = CASE WHEN attempts >= :max THEN 'failed' ELSE 'pending' END, "
                "next_attempt_at = now() + make_interval(mins => power(2, attempts)::int) "
                "WHERE delivery_id = :id"
            ),
            {
                "id": delivery_id,
                "err": f"{type(error).__name__}: {error}"[:1000],
                "max": MAX_ATTEMPTS,
            },
        )


async def purge() -> int:
    """Housekeeping: fail rows whose last attempt crashed, drop old finished ones."""
    async with SessionLocal() as s, s.begin():
        await s.execute(
            text(
                "UPDATE pr_gate_delivery SET status = 'failed', updated_at = now() "
                "WHERE status = 'processing' AND attempts >= :max AND next_attempt_at <= now()"
            ),
            {"max": MAX_ATTEMPTS},
        )
        gone = await s.execute(
            text(
                "DELETE FROM pr_gate_delivery WHERE status IN ('done', 'failed') "
                "AND updated_at < now() - make_interval(days => :days)"
            ),
            {"days": RETENTION_DAYS},
        )
        return gone.rowcount
