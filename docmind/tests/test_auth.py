"""P1-01 回归：DocMind 只接受 TaskFlow 签发的、带 uid 的 JWT。"""
import pytest
from app.api.deps import get_current_user_id
from app.core.config import settings
from app.main import app
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from shared.security import create_access_token


def credentials(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def make_token(
    *,
    user_id: int | None = 7,
    expires_minutes: int = 30,
    secret_key: str | None = None,
) -> str:
    return create_access_token(
        subject="alice",
        user_id=user_id,
        secret_key=secret_key or settings.JWT_SECRET_KEY,
        expires_minutes=expires_minutes,
    )


def test_valid_token_returns_uid():
    assert get_current_user_id(credentials(make_token(user_id=7))) == 7


def test_missing_credentials_returns_401():
    with pytest.raises(HTTPException) as exc_info:
        get_current_user_id(None)

    assert exc_info.value.status_code == 401


def test_token_without_uid_returns_401():
    token = make_token(user_id=None)

    with pytest.raises(HTTPException) as exc_info:
        get_current_user_id(credentials(token))

    assert exc_info.value.status_code == 401


def test_wrong_secret_returns_401():
    token = make_token(
        secret_key="wrong-secret-0123456789abcdef0123456789"
    )

    with pytest.raises(HTTPException) as exc_info:
        get_current_user_id(credentials(token))

    assert exc_info.value.status_code == 401


def test_expired_token_returns_401():
    token = make_token(expires_minutes=-1)

    with pytest.raises(HTTPException) as exc_info:
        get_current_user_id(credentials(token))

    assert exc_info.value.status_code == 401


def test_business_endpoints_require_auth():
    with TestClient(app) as client:
        responses = [
            client.get("/documents"),
            client.get("/conversations"),
            client.post("/conversations", json={"title": "x"}),
            client.post("/rag/query", json={"question": "hi"}),
        ]

    for response in responses:
        assert response.status_code == 401, response.text
