"""S1 headline proof: the API rejects tampered / expired / wrong-audience tokens.

Pure unit tests — no DB, no network, no running Keycloak. A throwaway RSA keypair
plays the role of Keycloak's signing key; its public half is the injected JWKS.
"""

from __future__ import annotations

import datetime
import json

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from easysynq_api.auth.jwks import JWKSCache
from easysynq_api.auth.tokens import authenticate
from easysynq_api.config import get_settings
from easysynq_api.problems import ProblemException

ISSUER = "https://kc.test/realms/easysynq"
AUDIENCE = "easysynq-api"
KID = "test-key"


@pytest.fixture
def _settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("OIDC_AUDIENCE", AUDIENCE)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def _keypair() -> tuple[rsa.RSAPrivateKey, dict[str, object]]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    jwk["kid"] = KID
    jwk["alg"] = "RS256"
    return key, {"keys": [jwk]}


def _mint(key: rsa.RSAPrivateKey, **overrides: object) -> str:
    now = datetime.datetime.now(tz=datetime.UTC)
    claims: dict[str, object] = {
        "sub": "kc-subject-1",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + datetime.timedelta(minutes=5)).timestamp()),
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": KID})


@pytest.mark.unit
async def test_valid_token_decodes(_settings, _keypair) -> None:
    key, jwks = _keypair
    claims = await authenticate(_mint(key), JWKSCache("", static_jwks=jwks))
    assert claims["sub"] == "kc-subject-1"


@pytest.mark.unit
async def test_expired_token_401(_settings, _keypair) -> None:
    key, jwks = _keypair
    past = int((datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=1)).timestamp())
    with pytest.raises(ProblemException) as ei:
        await authenticate(_mint(key, exp=past, iat=past), JWKSCache("", static_jwks=jwks))
    assert ei.value.status == 401
    assert ei.value.code == "token_expired"


@pytest.mark.unit
async def test_tampered_signature_401(_settings, _keypair) -> None:
    key, jwks = _keypair
    token = _mint(key)
    tampered = token[:-3] + ("aaa" if token[-3:] != "aaa" else "bbb")
    with pytest.raises(ProblemException) as ei:
        await authenticate(tampered, JWKSCache("", static_jwks=jwks))
    assert ei.value.status == 401
    assert ei.value.code == "token_invalid"


@pytest.mark.unit
async def test_wrong_audience_401(_settings, _keypair) -> None:
    key, jwks = _keypair
    with pytest.raises(ProblemException) as ei:
        await authenticate(_mint(key, aud="some-other-api"), JWKSCache("", static_jwks=jwks))
    assert ei.value.status == 401
    assert ei.value.code == "token_invalid"


@pytest.mark.unit
async def test_unknown_kid_401(_settings, _keypair) -> None:
    key, jwks = _keypair
    token = jwt.encode(
        {"sub": "x", "iss": ISSUER, "aud": AUDIENCE, "exp": 9999999999},
        key,
        algorithm="RS256",
        headers={"kid": "not-in-jwks"},
    )
    with pytest.raises(ProblemException) as ei:
        await authenticate(token, JWKSCache("", static_jwks=jwks))
    assert ei.value.status == 401
