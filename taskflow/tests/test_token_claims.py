"""共享 JWT 工具的契约测试。"""
import jwt
from shared.security import (
    AccessTokenClaims,
    create_access_token,
    decode_access_token,
    decode_access_token_claims,
)

SECRET = "unit-test-secret-0123456789abcdef"


def test_token_roundtrip_contains_uid():
    token = create_access_token(
        subject="alice",
        user_id=7,
        secret_key=SECRET,
    )

    claims = decode_access_token_claims(token, secret_key=SECRET)

    assert claims == AccessTokenClaims(subject="alice", user_id=7)


def test_missing_uid_is_allowed_by_decoder():
    token = create_access_token(subject="alice", secret_key=SECRET)

    claims = decode_access_token_claims(token, secret_key=SECRET)

    assert claims is not None
    assert claims.subject == "alice"
    assert claims.user_id is None


def test_compat_wrapper_returns_subject():
    token = create_access_token(subject="alice", secret_key=SECRET)

    assert decode_access_token(token, secret_key=SECRET) == "alice"


def test_wrong_secret_returns_none():
    token = create_access_token(
        subject="alice",
        user_id=7,
        secret_key="other-secret",
    )

    assert decode_access_token_claims(token, secret_key=SECRET) is None


def test_expired_token_returns_none():
    token = create_access_token(
        subject="alice",
        user_id=7,
        secret_key=SECRET,
        expires_minutes=-1,
    )

    assert decode_access_token_claims(token, secret_key=SECRET) is None


def test_uid_must_be_int_not_string():
    token = jwt.encode(
        {"sub": "alice", "uid": "7"},
        SECRET,
        algorithm="HS256",
    )

    assert decode_access_token_claims(token, secret_key=SECRET) is None


def test_uid_must_not_be_bool():
    token = jwt.encode(
        {"sub": "alice", "uid": True},
        SECRET,
        algorithm="HS256",
    )

    assert decode_access_token_claims(token, secret_key=SECRET) is None
