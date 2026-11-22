"""Test helpers: a configured GitHub App and mocked installation lookups (no real network)."""

import base64

import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import settings

API = "https://api.github.com"
_PEM = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)


def configure_app(monkeypatch) -> None:
    monkeypatch.setattr(settings, "github_app_id", "123")
    monkeypatch.setattr(settings, "github_private_key_base64", base64.b64encode(_PEM).decode())


def installed(
    repo: str,
    token: str = "ghs_t",
    installation: int = 5,
    router: respx.MockRouter = respx,
) -> respx.Route:
    """The App is installed on `repo`; returns the access-token route."""
    router.get(f"{API}/repos/{repo}/installation").respond(200, json={"id": installation})
    return router.post(f"{API}/app/installations/{installation}/access_tokens").respond(
        201, json={"token": token}
    )


def not_installed(repo: str, router: respx.MockRouter = respx) -> None:
    router.get(f"{API}/repos/{repo}/installation").respond(404, json={"message": "Not Found"})
