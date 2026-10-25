"""任务 ↔ 文档关联接口的回归测试。

覆盖：关联 / 列表 / 解除、幂等、归属校验、DocMind 故障语义，
以及 linked_only 检索只查已关联文档。
"""
import pytest
from app.clients.docmind import (
    DocMindDocumentNotFoundError,
    DocMindUnavailableError,
    get_docmind_client,
)
from app.main import app
from app.models.task_document import TaskDocument
from sqlmodel import Session, select


class FakeDocMindClient:
    def __init__(
            self,
            document: dict | None = None,
            document_error: Exception | None = None,
            results_by_document: dict[int, list[dict]] | None = None,
    ) -> None:
        self.document = document or {
            "id": 7,
            "filename": "spec.pdf",
            "status": "completed",
        }
        self.document_error = document_error
        self.results_by_document = results_by_document or {}
        self.document_calls: list[dict] = []
        self.search_calls: list[dict] = []

    async def get_document(self, *, document_id: int, token: str) -> dict:
        self.document_calls.append({
            "document_id": document_id,
            "token": token,
        })
        if self.document_error is not None:
            raise self.document_error
        return self.document

    async def search(
            self,
            *,
            query: str,
            token: str,
            top_k: int = 5,
            document_id: int | None = None,
            mode: str = "vector",
    ) -> list[dict]:
        self.search_calls.append({
            "query": query,
            "token": token,
            "top_k": top_k,
            "document_id": document_id,
        })
        return self.results_by_document.get(document_id, [])


@pytest.fixture
def fake_docmind():
    fake = FakeDocMindClient()
    app.dependency_overrides[get_docmind_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_docmind_client, None)


@pytest.fixture
def bob_headers(client):
    client.post(
        "/register",
        json={"username": "bob", "password": "FastAPI123"},
    )
    response = client.post(
        "/login",
        json={"username": "bob", "password": "FastAPI123"},
    )
    return {
        "Authorization": f"Bearer {response.json()['access_token']}"
    }


def _create_task(client, headers, title: str = "根据需求文档拆任务") -> int:
    response = client.post(
        "/tasks",
        headers=headers,
        json={"title": title},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _link(client, headers, task_id: int, document_id: int = 7):
    return client.post(
        f"/tasks/{task_id}/documents",
        headers=headers,
        json={"document_id": document_id},
    )


def test_requires_auth(client):
    response = client.get("/tasks/1/documents")

    assert response.status_code == 401


def test_link_and_list(client, alice_headers, alice_token, fake_docmind):
    task_id = _create_task(client, alice_headers)

    linked = _link(client, alice_headers, task_id)

    assert linked.status_code == 201, linked.text
    assert linked.json()["document_id"] == 7
    assert linked.json()["filename"] == "spec.pdf"
    # 文档归属由 DocMind 校验，token 必须原样透传。
    assert fake_docmind.document_calls == [{
        "document_id": 7,
        "token": alice_token,
    }]

    listed = client.get(
        f"/tasks/{task_id}/documents",
        headers=alice_headers,
    )

    assert listed.status_code == 200
    assert listed.json()["task_id"] == task_id
    assert len(listed.json()["items"]) == 1
    assert listed.json()["items"][0]["document_id"] == 7


def test_link_is_idempotent(client, alice_headers, fake_docmind):
    task_id = _create_task(client, alice_headers)

    first = _link(client, alice_headers, task_id)
    second = _link(client, alice_headers, task_id)

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["document_id"] == 7

    listed = client.get(
        f"/tasks/{task_id}/documents",
        headers=alice_headers,
    )
    # 唯一约束 + 幂等写入：不会出现两行。
    assert len(listed.json()["items"]) == 1


def test_unlink_is_idempotent(client, alice_headers, fake_docmind):
    task_id = _create_task(client, alice_headers)
    _link(client, alice_headers, task_id)

    first = client.delete(
        f"/tasks/{task_id}/documents/7",
        headers=alice_headers,
    )
    second = client.delete(
        f"/tasks/{task_id}/documents/7",
        headers=alice_headers,
    )

    assert first.status_code == 204
    assert second.status_code == 204

    listed = client.get(
        f"/tasks/{task_id}/documents",
        headers=alice_headers,
    )
    assert listed.json()["items"] == []


def test_deleting_task_removes_links(
        client,
        alice_headers,
        fake_docmind,
        test_engine,
):
    task_id = _create_task(client, alice_headers)
    _link(client, alice_headers, task_id)

    response = client.delete(
        f"/tasks/{task_id}",
        headers=alice_headers,
    )

    assert response.status_code == 204
    # 关联行必须随任务一起清理，否则 Postgres 的外键会阻止删任务。
    with Session(test_engine) as session:
        remaining = session.exec(
            select(TaskDocument).where(
                TaskDocument.task_id == task_id
            )
        ).all()
    assert remaining == []


def test_other_users_task_is_forbidden(
        client,
        alice_headers,
        bob_headers,
        fake_docmind,
):
    task_id = _create_task(client, alice_headers)

    response = client.post(
        f"/tasks/{task_id}/documents",
        headers=bob_headers,
        json={"document_id": 7},
    )

    assert response.status_code == 403
    # 任务归属先失败，不应白白调用 DocMind。
    assert fake_docmind.document_calls == []


def test_missing_document_returns_404(client, alice_headers, fake_docmind):
    fake_docmind.document_error = DocMindDocumentNotFoundError(
        "document 99 not found"
    )
    task_id = _create_task(client, alice_headers)

    response = _link(client, alice_headers, task_id, document_id=99)

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "document not found"


def test_docmind_unavailable_returns_502(
        client,
        alice_headers,
        fake_docmind,
):
    fake_docmind.document_error = DocMindUnavailableError("down")
    task_id = _create_task(client, alice_headers)

    response = _link(client, alice_headers, task_id)

    assert response.status_code == 502
    assert response.json()["error"]["message"] == (
        "knowledge service unavailable"
    )


def _chunk(document_id: int, *, score: float, content: str) -> dict:
    return {
        "document_id": document_id,
        "filename": f"doc-{document_id}.pdf",
        "chunk_index": 0,
        "page": 1,
        "content": content,
        "score": score,
    }


def test_linked_only_searches_only_linked_documents(
        client,
        alice_headers,
        fake_docmind,
):
    fake_docmind.results_by_document = {
        7: [_chunk(7, score=0.5, content="文档 7 的内容")],
        8: [_chunk(8, score=0.9, content="文档 8 的内容")],
        # 未关联的 9 号文档即使有命中也不该被搜到。
        9: [_chunk(9, score=0.99, content="文档 9 的内容")],
    }
    task_id = _create_task(client, alice_headers)
    _link(client, alice_headers, task_id, document_id=7)
    _link(client, alice_headers, task_id, document_id=8)

    response = client.get(
        f"/tasks/{task_id}/knowledge",
        headers=alice_headers,
        params={"linked_only": True, "top_k": 5},
    )

    assert response.status_code == 200, response.text
    contents = [item["content"] for item in response.json()["results"]]
    # 按分数合并：8 在前、7 在后，且完全没有 9。
    assert contents == ["文档 8 的内容", "文档 7 的内容"]
    searched_ids = {
        call["document_id"]
        for call in fake_docmind.search_calls
    }
    assert searched_ids == {7, 8}


def test_linked_only_without_links_returns_empty(
        client,
        alice_headers,
        fake_docmind,
):
    task_id = _create_task(client, alice_headers)

    response = client.get(
        f"/tasks/{task_id}/knowledge",
        headers=alice_headers,
        params={"linked_only": True},
    )

    assert response.status_code == 200
    assert response.json()["results"] == []
    assert fake_docmind.search_calls == []
