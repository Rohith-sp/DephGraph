"""Durable PR-gate deliveries: idempotent redelivery, retry with backoff, crash recovery, purge,
and concurrent sweepers never claiming the same row."""

import asyncio
import json
import uuid

import pytest
import respx
from sqlalchemy import text

from app.database import SessionLocal
from app.services import pr_gate_queue as queue
from tests.test_pr_gate import (  # noqa: F401 (_cfg: autouse)
    FILES,
    _cfg,
    files,
    mkpkg,
    payload,
    post,
    review_rows,
)

CRON = {"X-Cron-Secret": "test-cron-secret"}


def delivery() -> dict[str, str]:
    return {"X-GitHub-Delivery": uuid.uuid4().hex}


async def row(delivery_id: str) -> dict:
    async with SessionLocal() as s:
        found = await s.execute(
            text(
                "SELECT status, attempts, last_error, next_attempt_at > now() AS waiting "
                "FROM pr_gate_delivery WHERE delivery_id = :i"
            ),
            {"i": delivery_id},
        )
        return dict(found.mappings().one())


async def make_due(delivery_id: str, **set_: str) -> None:
    cols = ", ".join(
        f"{k} = {v}" for k, v in {"next_attempt_at": "now() - interval '1 second'", **set_}.items()
    )
    async with SessionLocal() as s, s.begin():
        await s.execute(
            text(f"UPDATE pr_gate_delivery SET {cols} WHERE delivery_id = :i"), {"i": delivery_id}
        )


async def sweep(client) -> dict:
    r = await client.post("/internal/pr-gate/retry", headers=CRON)
    assert r.status_code == 200, r.text
    return r.json()["data"]


@respx.mock
async def test_redelivery_is_a_noop(client, auth, reseed):
    await mkpkg("dupdep", cvss=9.8)
    route = files("dupdep")
    hdr = delivery()
    first = await post(client, payload(), headers_extra=hdr)
    assert first.status_code == 200 and first.json()["data"]["pr_number"] == 7
    again = await post(client, payload(), headers_extra=hdr)
    assert again.status_code == 200 and again.json()["data"] == {"duplicate": True}
    assert route.call_count == 1  # GitHub was asked once
    assert (await row(hdr["X-GitHub-Delivery"]))["status"] == "done"
    assert [x["dep_name"] for x in await review_rows(client, auth)] == ["dupdep"]


@respx.mock
async def test_failed_review_is_retried_by_the_sweeper(client, auth, reseed):
    await mkpkg("retrydep", cvss=9.8)
    hdr = delivery()
    did = hdr["X-GitHub-Delivery"]
    respx.get(FILES).respond(500)
    r = await post(client, payload(), headers_extra=hdr)
    assert r.status_code == 502
    got = await row(did)
    assert (got["status"], got["attempts"], got["waiting"]) == ("pending", 1, True)
    assert got["last_error"]
    assert await sweep(client) == {"processed": 0, "failed": 0, "purged": 0}  # backoff not over

    respx.clear()
    files("retrydep")
    await make_due(did)
    assert await sweep(client) == {"processed": 1, "failed": 0, "purged": 0}
    assert (await row(did))["status"] == "done" and (await row(did))["attempts"] == 2
    assert [x["dep_name"] for x in await review_rows(client, auth)] == ["retrydep"]


async def test_crashed_review_is_retried_after_its_lease(client):
    did = uuid.uuid4().hex
    assert await queue.record(did, "pull_request", payload()) is True  # claimed, then "crashes"
    assert await queue.claim() is None  # lease still running: another worker owns it
    await make_due(did)
    claimed = await queue.claim()
    assert claimed and claimed[0] == did and claimed[1]["number"] == 7
    assert (await row(did))["attempts"] == 2


async def test_backoff_doubles_and_gives_up_after_the_cap(client):
    did = uuid.uuid4().hex
    await queue.record(did, "pull_request", payload())
    waits = []
    for attempt in range(1, queue.MAX_ATTEMPTS + 1):
        if attempt > 1:
            await make_due(did)
            assert (await queue.claim())[0] == did
        await queue.fail(did, RuntimeError("boom"))
        async with SessionLocal() as s:
            waits.append(
                (
                    await s.execute(
                        text(
                            "SELECT extract(epoch FROM next_attempt_at - now()) / 60 FROM "
                            "pr_gate_delivery WHERE delivery_id = :i"
                        ),
                        {"i": did},
                    )
                ).scalar_one()
            )
    assert [round(w) for w in waits] == [2, 4, 8, 16, 32]
    assert (await row(did))["status"] == "failed" and (await row(did))[
        "last_error"
    ] == "RuntimeError: boom"
    await make_due(did)
    assert await queue.claim() is None  # terminal


async def test_purge_drops_old_finished_rows_and_fails_exhausted_crashes(client):
    old, fresh, crashed, pending = (uuid.uuid4().hex for _ in range(4))
    for d in (old, fresh, crashed, pending):
        await queue.record(d, "pull_request", payload())
    await queue.done(old)
    await queue.done(fresh)
    await make_due(old, updated_at="now() - interval '31 days'")
    await make_due(crashed, attempts=str(queue.MAX_ATTEMPTS))
    assert await queue.purge() == 1
    async with SessionLocal() as s:
        left = {
            r[0]: r[1]
            for r in await s.execute(
                text(
                    "SELECT delivery_id, status FROM pr_gate_delivery WHERE delivery_id = ANY(:i)"
                ),
                {"i": [old, fresh, crashed, pending]},
            )
        }
    assert left == {fresh: "done", crashed: "failed", pending: "processing"}


async def test_concurrent_sweepers_never_claim_the_same_row(client):
    ids = [uuid.uuid4().hex for _ in range(8)]
    for d in ids:
        await queue.record(d, "pull_request", payload())
        await make_due(d)
    got = [c for c in await asyncio.gather(*(queue.claim() for _ in range(12))) if c]
    mine = [c[0] for c in got if c[0] in ids]
    assert sorted(mine) == sorted(ids)  # each of the 8 exactly once; the extra claims got nothing


async def test_retry_endpoint_needs_the_cron_secret(client):
    assert (await client.post("/internal/pr-gate/retry")).status_code == 401
