import pytest


# conftest.py 已提供 alice_headers(注册 + 登录 alice)
# 这里再提供一个 bob 用户,用来验证"任务只属于创建者"这个隔离规则。
@pytest.fixture
def bob_headers(client,):
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
    return {
        "Authorization":
        f"Bearer {response.json()['access_token']}"
    }


def _create_task(client, headers, title="Learn FastAPI", done=False):
    """创建一条任务并返回原始响应,方便各测试复用。"""
    return client.post(
        "/tasks",
        headers=headers,
        json={
            "title": title,
            "description": "pytest practice",
            "done": done,
        },
    )


# ---------- 创建 ----------

def test_create_task(client, alice_headers,):
    response = _create_task(
        client,
        alice_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Learn FastAPI"
    assert data["description"] == "pytest practice"
    assert data["done"] is False
    assert isinstance(data["id"], int)


def test_create_task_requires_auth(client,):
    response = client.post(
        "/tasks",
        json={"title": "no auth"},
    )
    assert response.status_code == 401
    assert (
        response.json()
        ["error"]["code"]
        == "UNAUTHORIZED"
    )


def test_create_task_blank_title(client, alice_headers,):
    # title 全空格会被 field_validator strip 后判为非法
    response = client.post(
        "/tasks",
        headers=alice_headers,
        json={"title": "   "},
    )
    assert response.status_code == 422
    assert (
        response.json()
        ["error"]["code"]
        == "VALIDATION_ERROR"
    )


# ---------- 列表 ----------

def test_get_tasks_empty(client, alice_headers,):
    response = client.get(
        "/tasks?page=1&page_size=10",
        headers=alice_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["tasks"] == []


def test_get_tasks_requires_auth(client,):
    response = client.get(
        "/tasks?page=1&page_size=10",
    )
    assert response.status_code == 401


def test_get_tasks_pagination_and_filters(client, alice_headers,):
    for i in range(3):
        _create_task(client, alice_headers, title=f"task-{i}")
    _create_task(client, alice_headers, title="learn-done", done=True)

    # 分页:page_size=2 时第一页只返回 2 条,但 total 是全部 4 条
    response = client.get(
        "/tasks?page=1&page_size=2",
        headers=alice_headers,
    )
    data = response.json()
    assert data["total"] == 4
    assert len(data["tasks"]) == 2

    # keyword 过滤:只匹配标题里含 "task-" 的 3 条
    response = client.get(
        "/tasks?keyword=task-",
        headers=alice_headers,
    )
    data = response.json()
    assert data["total"] == 3

    # done 过滤:只匹配已完成的那条
    response = client.get(
        "/tasks?done=true",
        headers=alice_headers,
    )
    data = response.json()
    assert data["total"] == 1
    assert data["tasks"][0]["title"] == "learn-done"


# ---------- 单条查询 ----------

def test_get_task_by_id(client, alice_headers,):
    task_id = _create_task(client, alice_headers).json()["id"]
    response = client.get(
        f"/tasks/{task_id}",
        headers=alice_headers,
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Learn FastAPI"


def test_get_task_not_found(client, alice_headers,):
    response = client.get(
        "/tasks/999",
        headers=alice_headers,
    )
    assert response.status_code == 404
    assert (
        response.json()
        ["error"]["code"]
        == "TASK_NOT_FOUND"
    )


# ---------- 更新 ----------

def test_update_task(client, alice_headers,):
    task_id = _create_task(client, alice_headers).json()["id"]
    response = client.put(
        f"/tasks/{task_id}",
        headers=alice_headers,
        json={
            "title": "updated",
            "description": "changed",
            "done": True,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "updated"
    assert data["description"] == "changed"
    assert data["done"] is True


def test_partial_update_task(client, alice_headers,):
    task_id = _create_task(client, alice_headers).json()["id"]
    response = client.patch(
        f"/tasks/{task_id}",
        headers=alice_headers,
        json={"title": "patched"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "patched"
    # 没传的字段保持不变
    assert data["done"] is False
    assert data["description"] == "pytest practice"


# ---------- 删除 ----------

def test_delete_task(client, alice_headers,):
    task_id = _create_task(client, alice_headers).json()["id"]
    response = client.delete(
        f"/tasks/{task_id}",
        headers=alice_headers,
    )
    assert response.status_code == 204

    # 删除后再查,应返回 404
    response = client.get(
        f"/tasks/{task_id}",
        headers=alice_headers,
    )
    assert response.status_code == 404


# ---------- 所有权隔离 ----------

def test_task_ownership_isolation(client, alice_headers, bob_headers,):
    task_id = _create_task(client, alice_headers).json()["id"]

    # bob 的任务列表里看不到 alice 的任务
    response = client.get("/tasks", headers=bob_headers)
    assert response.json()["total"] == 0

    # bob 读取 / 修改 / 删除 alice 的任务 → 403
    response = client.get(
        f"/tasks/{task_id}",
        headers=bob_headers,
    )
    assert response.status_code == 403
    assert (
        response.json()
        ["error"]["code"]
        == "TASK_FORBIDDEN"
    )

    response = client.delete(
        f"/tasks/{task_id}",
        headers=bob_headers,
    )
    assert response.status_code == 403

    # alice 本人不受影响,任务还在
    response = client.get(
        f"/tasks/{task_id}",
        headers=alice_headers,
    )
    assert response.status_code == 200