import time

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jose import JWTError, jwk, jwt

from app import auth
from app.config import settings

JWKS_URL = "http://supabase.test/auth/v1/.well-known/jwks.json"


def _keypair(kid: str):
    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub = priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return pem, {**jwk.construct(pub, "ES256").to_dict(), "kid": kid}


def _token(pem: bytes, kid: str, aud: str = "authenticated", exp: int = 600) -> str:
    claims = {"sub": "u1", "aud": aud, "exp": int(time.time()) + exp}
    return jwt.encode(claims, pem, algorithm="ES256", headers={"kid": kid})


@pytest.fixture(autouse=True)
def jwks_state(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "http://supabase.test")
    monkeypatch.setattr(auth, "_jwks", {})
    monkeypatch.setattr(auth, "_jwks_fetched", 0.0)


@respx.mock
async def test_es256_verified_via_jwks():
    pem, pub = _keypair("k1")
    route = respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json={"keys": [pub]}))
    t = _token(pem, "k1")
    assert (await auth.decode_token(t))["sub"] == "u1"
    await auth.decode_token(t)
    assert route.call_count == 1  # cached


@respx.mock
async def test_es256_rejects_bad_audience_expired_and_foreign_key():
    pem, pub = _keypair("k1")
    other, _ = _keypair("k1")
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json={"keys": [pub]}))
    for t in (_token(pem, "k1", aud="anon"), _token(pem, "k1", exp=-10), _token(other, "k1")):
        with pytest.raises(JWTError):
            await auth.decode_token(t)


@respx.mock
async def test_unknown_kid_refetches_once_then_cooldown():
    pem, pub = _keypair("new")
    route = respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json={"keys": [pub]}))
    with pytest.raises(JWTError):
        await auth.decode_token(_token(pem, "forged"))
    with pytest.raises(JWTError):
        await auth.decode_token(_token(pem, "forged-2"))
    assert route.call_count == 1  # forged kids cannot trigger repeated fetches


@respx.mock
async def test_jwks_outage_is_a_401(client):
    respx.get(JWKS_URL).mock(return_value=httpx.Response(500))
    pem, _ = _keypair("k1")
    r = await client.get("/services", headers={"Authorization": f"Bearer {_token(pem, 'k1')}"})
    assert r.status_code == 401


@respx.mock
async def test_es256_token_accepted_by_api(client):
    pem, pub = _keypair("k1")
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json={"keys": [pub]}))
    r = await client.get("/services", headers={"Authorization": f"Bearer {_token(pem, 'k1')}"})
    assert r.status_code == 200


async def test_hs256_still_verified_with_secret():
    t = jwt.encode(
        {"sub": "u1", "aud": "authenticated", "exp": int(time.time()) + 60},
        settings.supabase_jwt_secret,
        algorithm="HS256",
    )
    assert (await auth.decode_token(t))["sub"] == "u1"
