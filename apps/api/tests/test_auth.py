import base64
import hashlib
import hmac
import json
import time

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jwt import PyJWTError

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
    return pem, {
        **json.loads(jwt.algorithms.ECAlgorithm.to_jwk(serialization.load_pem_public_key(pub))),
        "kid": kid,
    }


def _token(pem: bytes, kid: str, aud: str = "authenticated", exp: int = 600) -> str:
    claims = {
        "sub": "00000000-0000-0000-0000-000000000001",
        "aud": aud,
        "exp": int(time.time()) + exp,
    }
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
    assert (await auth.decode_token(t))["sub"] == "00000000-0000-0000-0000-000000000001"
    await auth.decode_token(t)
    assert route.call_count == 1  # cached


@respx.mock
async def test_es256_rejects_bad_audience_expired_and_foreign_key():
    pem, pub = _keypair("k1")
    other, _ = _keypair("k1")
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json={"keys": [pub]}))
    for t in (
        _token(pem, "k1", aud="anon"),
        _token(pem, "k1", exp=-10),
        _token(other, "k1"),
    ):
        with pytest.raises(PyJWTError):
            await auth.decode_token(t)


@respx.mock
async def test_unknown_kid_refetches_once_then_cooldown():
    pem, pub = _keypair("new")
    route = respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json={"keys": [pub]}))
    with pytest.raises(PyJWTError):
        await auth.decode_token(_token(pem, "forged"))
    with pytest.raises(PyJWTError):
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
        {
            "sub": "00000000-0000-0000-0000-000000000001",
            "aud": "authenticated",
            "exp": int(time.time()) + 60,
        },
        settings.supabase_jwt_secret,
        algorithm="HS256",
    )
    assert (await auth.decode_token(t))["sub"] == "00000000-0000-0000-0000-000000000001"


@respx.mock
async def test_hs256_signed_with_public_key_is_rejected():
    """Algorithm confusion: attacker HMACs with the published public key as the secret."""
    pem, pub = _keypair("k1")
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json={"keys": [pub]}))
    pub_pem = jwt.PyJWK(pub).key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    claims = {
        "sub": "00000000-0000-0000-0000-000000000001",
        "aud": "authenticated",
        "exp": int(time.time()) + 60,
    }
    # PyJWT itself refuses to HMAC with a PEM key, so build the forgery by hand
    signing = (
        base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT","kid":"k1"}').rstrip(b"=")
        + b"."
        + base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
    )
    sig = base64.urlsafe_b64encode(hmac.new(pub_pem, signing, hashlib.sha256).digest())
    with pytest.raises(PyJWTError):
        await auth.decode_token((signing + b"." + sig.rstrip(b"=")).decode())


async def test_alg_none_is_rejected():
    t = jwt.encode(
        {
            "sub": "00000000-0000-0000-0000-000000000001",
            "aud": "authenticated",
            "exp": int(time.time()) + 60,
        },
        None,
        algorithm="none",
    )
    with pytest.raises(PyJWTError):
        await auth.decode_token(t)
