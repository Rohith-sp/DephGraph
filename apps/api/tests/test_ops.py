"""Operational hardening: security headers, Sentry scrubbing/opt-in, free-tier keep-alive."""

import pytest
import sentry_sdk

from app import observability
from app.config import settings
from app.database import get_redis
from app.observability import SECURITY_HEADERS, configure_sentry, scrub_event

CRON = {"X-Cron-Secret": "test-cron-secret"}


@pytest.mark.parametrize(
    "call",
    [
        lambda c, a: c.get("/health"),
        lambda c, a: c.get("/services", headers=a),
        lambda c, a: c.get("/services"),  # 401
        lambda c, a: c.get("/nope"),  # 404
    ],
)
async def test_every_response_carries_the_security_headers(client, auth, call):
    r = await call(client, auth)
    assert {k: r.headers.get(k) for k in SECURITY_HEADERS} == SECURITY_HEADERS


async def test_rate_limited_response_carries_them_too(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit", "1/minute")
    headers = {"X-Forwarded-For": "203.0.113.77"}
    await client.get("/services", headers=headers)
    r = await client.get("/services", headers=headers)
    assert r.status_code == 429 and r.headers["X-Frame-Options"] == "DENY"


def test_sentry_event_is_scrubbed():
    event = {
        "request": {
            "headers": {
                "Authorization": "Bearer x",
                "X-Hub-Signature-256": "sha256=y",
                "x-cron-secret": "z",
                "User-Agent": "GitHub-Hookshot",
            },
            "cookies": {"s": "1"},
            "data": {"pull_request": "..."},
        }
    }
    assert scrub_event(event, None) == {"request": {"headers": {"User-Agent": "GitHub-Hookshot"}}}
    assert scrub_event({"message": "no request"}, None) == {"message": "no request"}


def test_sentry_starts_only_with_a_dsn(monkeypatch):
    started = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: started.append(kw))
    configure_sentry()
    assert started == []
    monkeypatch.setattr(settings, "sentry_dsn", "https://key@o0.ingest.sentry.io/1")
    monkeypatch.setattr(settings, "sentry_traces_sample_rate", 0.25)
    configure_sentry()
    assert started[0]["traces_sample_rate"] == 0.25
    assert started[0]["send_default_pii"] is False
    assert started[0]["before_send"] is observability.scrub_event


async def test_keepalive_touches_all_three_stores(client):
    assert (await client.post("/internal/keepalive")).status_code == 401
    r = await client.post("/internal/keepalive", headers=CRON)
    assert r.status_code == 200 and r.json()["data"] == {
        "postgres": True,
        "mongo": True,
        "redis": True,
    }


async def test_keepalive_names_the_store_that_is_down(client, monkeypatch):
    async def down():
        raise OSError("down")

    monkeypatch.setattr(get_redis(), "ping", down)
    r = await client.post("/internal/keepalive", headers=CRON)
    assert r.status_code == 503 and r.json()["error"] == "Unreachable: redis"
