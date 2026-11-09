from types import SimpleNamespace

import jwt
import pytest

from app.core import security


def test_password_hash_round_trip():
    encoded = security.hash_password("correct-horse-battery-staple")
    assert encoded != "correct-horse-battery-staple"
    assert security.verify_password("correct-horse-battery-staple", encoded)
    assert not security.verify_password("incorrect-password", encoded)


def test_access_token_contains_subject_and_role(monkeypatch):
    monkeypatch.setattr(security.settings, "auth_secret_key", "a" * 48)
    monkeypatch.setattr(security.settings, "auth_token_issuer", "test-video-auditor")
    user = SimpleNamespace(id="user-123", role="reviewer")
    token, expires_in = security.create_access_token(user)
    claims = security.decode_access_token(token)
    assert claims["sub"] == "user-123"
    assert claims["role"] == "reviewer"
    assert expires_in == security.settings.auth_access_token_minutes * 60


def test_access_token_rejects_wrong_issuer(monkeypatch):
    monkeypatch.setattr(security.settings, "auth_secret_key", "a" * 48)
    monkeypatch.setattr(security.settings, "auth_token_issuer", "expected")
    token = jwt.encode(
        {"sub": "user-123", "iss": "wrong", "iat": 1, "exp": 4102444800},
        "a" * 48,
        algorithm="HS256",
    )
    with pytest.raises(jwt.InvalidTokenError):
        security.decode_access_token(token)
