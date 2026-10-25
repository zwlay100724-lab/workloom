"""任务详情「相关知识」接口的回归测试。

DocMind 用 fake client 覆盖，不真的起第二个服务；
验证：鉴权、归属、query 拼接、token 透传、字段映射、下游故障时的 502。
"""
import pytest
from app.clients.docmind import DocMindUnavailableError, get_docmind_client
from app.main import app


class FakeDocMindClient:
    def __init__(
            self,
            results: list[dict] | None = None,
            error: Exception | None = None,
    ) -> None:
        self.results = results or []
        self.error = error
        self.calls: list[dict] = []

    async def search(
            self,
            *,
            query: str,
            token: str,
            top_k: int = 5,
            document_id: int | None = None,
            mode: str = "vector",
    ) -> list[dict]:
        self.calls.append({
            "query": query,
            "token": token,
            "top_k": top_k,
            "document_id": document_id,
            "mode": mode,
        })
        if self.error is not None:
            raise self.error
        return self.results


@pytest.fixture
def fake_docmind():
    fake = FakeDocMindClient()
    app.dependency_overrides[get_docmind_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_docmind_client, None)


def _create_task(
        client,
        headers,
        *,
        title: str = "整理周报",
        description: str = "总结本周进展",
) -> int:
    response = client.post(
        "/tasks",
        headers=headers,
        json={
            "title": title,
            "description": description,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _register_and_login(client, username: str) -> dict[str, str]:
    client.post(
        "/register",
        json={
            "username": username,
            "password": "FastAPI123",
        },
    )
    response = client.post(
        "/login",
        json={
            "username": username,
            "password": "FastAPI123",
        },
    )
    return {
        "Authorization": f"Bearer {response.json()['access_token']}"
    }


def test_requires_auth(client):
    response = client.get("/tasks/1/knowledge")

    assert response.status_code == 401


def test_returns_knowledge_chunks(
        client,
        alice_headers,
        alice_token,
        fake_docmind,
):
    fake_docmind.results = [
        {
            "document_id": 7,
            "filename": "handbook.pdf",
            "chunk_index": 2,
            "page": 3,
            "content": "员工每年享有 10 天带薪年假。",
            "score": 0.91,
        }
    ]
    task_id = _create_task(client, alice_headers)

    response = client.get(
        f"/tasks/{task_id}/knowledge?top_k=3",
        headers=alice_headers,
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["task_id"] == task_id
    assert data["query"] == "整理周报 总结本周进展"
    assert data["results"] == [
        {
            "document_id": 7,
            "filename": "handbook.pdf",
            "chunk_index": 2,
            "page": 3,
            "content": "员工每年享有 10 天带薪年假。",
            "score": 0.91,
        }
    ]
    # token 必须原样透传，DocMind 才能解析出同一个 uid。
    assert fake_docmind.calls == [
        {
            "query": "整理周报 总结本周进展",
            "token": alice_token,
            "top_k": 3,
            "document_id": None,
            "mode": "vector",
        }
    ]


def test_description_is_optional(
        client,
        alice_headers,
        fake_docmind,
):
    task_id = _create_task(
        client,
        alice_headers,
        title="只看标题",
        description=None,
    )

    response = client.get(
        f"/tasks/{task_id}/knowledge",
        headers=alice_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["query"] == "只看标题"
    assert fake_docmind.calls[0]["query"] == "只看标题"


def test_other_users_task_is_forbidden(
        client,
        alice_headers,
        fake_docmind,
):
    task_id = _create_task(client, alice_headers)
    bob_headers = _register_and_login(client, "bob")

    response = client.get(
        f"/tasks/{task_id}/knowledge",
        headers=bob_headers,
    )

    # 任务存在但不属于当前用户 → 403（沿用任务资源的既有语义）。
    assert response.status_code == 403
    assert fake_docmind.calls == []


def test_missing_task_returns_404(
        client,
        alice_headers,
        fake_docmind,
):
    response = client.get(
        "/tasks/999999/knowledge",
        headers=alice_headers,
    )

    assert response.status_code == 404
    assert fake_docmind.calls == []


def test_docmind_failure_returns_502(
        client,
        alice_headers,
        fake_docmind,
):
    fake_docmind.error = DocMindUnavailableError("DocMind is down")
    task_id = _create_task(client, alice_headers)

    response = client.get(
        f"/tasks/{task_id}/knowledge",
        headers=alice_headers,
    )

    assert response.status_code == 502
    # shared 的 HTTPException handler 把错误统一成 {success, error:{...}}。
    assert response.json()["error"]["message"] == (
        "knowledge service unavailable"
    )
