import pytest
from fastapi import Request

from app.config import settings
from app.limiter import client_ip, storage
from app.main import app


@pytest.fixture
def low_limit(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit", "3/minute")
    storage.reset()
    yield
    storage.reset()


@pytest.fixture
def boom():
    @app.get("/_boom")
    async def _boom() -> None:
        raise RuntimeError("secret internals")

    yield
    app.router.routes.pop()


async def test_rate_limit_429_envelope(client, low_limit):
    codes = [(await client.get("/services")).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]
    r = await client.get("/services")
    assert r.json() == {"data": None, "error": "Rate limit exceeded"}
    assert r.headers["x-request-id"]


async def test_health_exempt_from_limit(client, low_limit):
    assert [(await client.get("/health")).status_code for _ in range(6)] == [200] * 6


async def test_unhandled_exception_500_envelope(client, boom):
    r = await client.get("/_boom")
    assert r.status_code == 500
    assert r.json() == {"data": None, "error": "Internal server error"}
    assert "secret internals" not in r.text
    assert r.headers["x-request-id"]


async def test_request_id_generated_and_echoed(client):
    assert len((await client.get("/health")).headers["x-request-id"]) == 32
    r = await client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"
    r = await client.get("/health", headers={"X-Request-ID": "bad id\r\n"})
    assert r.headers["x-request-id"] != "bad id\r\n"


def _req(xff: str | None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "headers": headers, "client": ("10.0.0.9", 1)})


def test_client_ip_trusts_only_last_proxy_hop(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_hops", 1)
    assert client_ip(_req("6.6.6.6, 1.2.3.4")) == "1.2.3.4"  # left entry is spoofable
    assert client_ip(_req(None)) == "10.0.0.9"
    monkeypatch.setattr(settings, "trusted_proxy_hops", 0)
    assert client_ip(_req("1.2.3.4")) == "10.0.0.9"
