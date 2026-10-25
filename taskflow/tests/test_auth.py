from app.core.config import settings
from shared.security import decode_access_token_claims


def test_register_user(
    client,
):
    response = client.post(
        "/register",
        json={
            "username": "alice",
            "password": "FastAPI123",
        },
    )

    assert (response.status_code== 201)
    data = response.json()
    assert data["username"] == "alice"
    assert "password" not in data
    assert "hashed_password" not in data

def test_register_duplicate_username(client,):
    payload = {"username": "alice","password": "FastAPI123",}
    client.post(
        "/register",
        json=payload,
    )
    response = client.post(
        "/register",
        json=payload,
    )
    assert (response.status_code== 409)
    data = response.json()

    assert (
        data["error"]["code"]
        == "USERNAME_ALREADY_EXISTS"
    )

def test_login_success(
    client,
):

    client.post(
        "/register",
        json={
            "username": "alice",
            "password": "FastAPI123",
        },
    )

    response = client.post(
        "/login",
        json={
            "username": "alice",
            "password": "FastAPI123",
        },
    )

    assert (response.status_code== 200 )
    data = response.json()
    assert "access_token" in data
    assert (data["token_type"]== "bearer")


def test_login_token_contains_uid(alice_token):
    """TaskFlow 签发的 token 必须带 uid，DocMind 才能做归属过滤。"""
    claims = decode_access_token_claims(
        alice_token,
        secret_key=settings.jwt_secret_key,
    )

    assert claims is not None
    assert claims.subject == "alice"
    assert isinstance(claims.user_id, int)


def test_login_wrong_password(
    client,
):

    client.post(
        "/register",
        json={
            "username": "alice",
            "password": "FastAPI123",
        },
    )

    response = client.post(
        "/login",
        json={
            "username": "alice",
            "password": "wrong-password",
        },
    )

    assert (
        response.status_code
        == 401
    )

    assert (
        response.json()
        ["error"]["code"]
        == "INVALID_CREDENTIALS"
    )