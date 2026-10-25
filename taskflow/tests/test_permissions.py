import pytest


@pytest.fixture
def bob_token(
    client,
):

    client.post(
        "/register",
        json={
            "username": "bob",
            "password": "FastAPI123",
        },
    )

    response = client.post(
        "/login",
        json={
            "username": "bob",
            "password": "FastAPI123",
        },
    )

    return response.json()[
        "access_token"
    ]


@pytest.fixture
def bob_headers(
    bob_token,
):

    return {
        "Authorization":
        f"Bearer {bob_token}"
    }

def test_user_only_sees_own_tasks(
    client,
    alice_headers,
    bob_headers,
):

    client.post(
        "/tasks",
        headers=alice_headers,
        json={
            "title": "Alice Task",
        },
    )

    client.post(
        "/tasks",
        headers=bob_headers,
        json={
            "title": "Bob Task",
        },
    )

    response = client.get(
        "/tasks",
        headers=alice_headers,
    )

    assert (
        response.status_code
        == 200
    )

    data = response.json()

    assert data["total"] == 1

    assert (
        data["tasks"][0]["title"]
        == "Alice Task"
    )

def test_user_cannot_get_other_users_task(
    client,
    alice_headers,
    bob_headers,
):

    bob_task = client.post(
        "/tasks",
        headers=bob_headers,
        json={
            "title": "Bob Private Task",
        },
    ).json()

    response = client.get(
        f"/tasks/{bob_task['id']}",
        headers=alice_headers,
    )

    assert (
        response.status_code
        == 403
    )

def test_user_cannot_update_other_users_task(
    client,
    alice_headers,
    bob_headers,
):

    bob_task = client.post(
        "/tasks",
        headers=bob_headers,
        json={
            "title": "Bob Task",
        },
    ).json()

    response = client.put(
        f"/tasks/{bob_task['id']}",
        headers=alice_headers,
        json={
            "title": "Hacked",
            "description": None,
            "done": True,
        },
    )

    assert (
        response.status_code
        == 403
    )

def test_user_cannot_delete_other_users_task(
    client,
    alice_headers,
    bob_headers,
):

    bob_task = client.post(
        "/tasks",
        headers=bob_headers,
        json={
            "title": "Bob Task",
        },
    ).json()

    response = client.delete(
        f"/tasks/{bob_task['id']}",
        headers=alice_headers,
    )

    assert (
        response.status_code
        == 403
    )

    bob_response = client.get(
        f"/tasks/{bob_task['id']}",
        headers=bob_headers,
    )

    assert (
        bob_response.status_code
        == 200
    )
    bob_response = client.get(
        f"/tasks/{bob_task['id']}",
        headers=bob_headers,
    )

    assert (
        bob_response.status_code
        == 200
    )
def test_get_tasks_without_token(
    client,
):

    response = client.get(
        "/tasks"
    )

    assert (
        response.status_code
        == 401
    )
def test_get_tasks_with_invalid_token(
    client,
):

    response = client.get(
        "/tasks",
        headers={
            "Authorization":
            "Bearer invalid-token"
        },
    )

    assert (
        response.status_code
        == 401
    )
