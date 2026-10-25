"""「文档 → 任务」接口的回归测试。

DocMind 和 LLM 都用 fake 覆盖，验证：
- 文档归属 / 索引状态的前置校验；
- 检索范围锁定在指定文档；
- 生成的任务带来源引用，并自动写入 task_documents 关联；
- 下游故障（DocMind / LLM）的错误语义。
"""
from datetime import date

import pytest
from app.clients.docmind import (
    DocMindDocumentNotFoundError,
    DocMindUnavailableError,
    get_docmind_client,
)
from app.main import app
from app.schemas.generated import GeneratedTaskSpec
from app.services.llm_service import get_llm_service
from shared.exceptions import LLMStructuredOutputError


class FakeDocMindClient:
    def __init__(
            self,
            document: dict | None = None,
            document_error: Exception | None = None,
            chunks: list[dict] | None = None,
            search_error: Exception | None = None,
    ) -> None:
        self.document = document or {
            "id": 7,
            "filename": "plan.pdf",
            "status": "completed",
        }
        self.document_error = document_error
        self.chunks = chunks if chunks is not None else [
            {
                "document_id": 7,
                "filename": "plan.pdf",
                "chunk_index": 0,
                "page": 1,
                "content": "下周一前完成登录接口联调。",
                "score": 0.9,
            },
            {
                "document_id": 7,
                "filename": "plan.pdf",
                "chunk_index": 5,
                "page": 3,
                "content": "测试用例需要覆盖 token 过期场景。",
                "score": 0.8,
            },
        ]
        self.search_error = search_error
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
            "top_k": top_k,
            "document_id": document_id,
        })
        if self.search_error is not None:
            raise self.search_error
        return self.chunks


class FakeLLMService:
    def __init__(
            self,
            specs: list[GeneratedTaskSpec] | None = None,
            error: Exception | None = None,
    ) -> None:
        self.specs = specs if specs is not None else []
        self.error = error
        self.calls: list[dict] = []

    async def extract_tasks_from_document(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.specs


@pytest.fixture
def fakes():
    docmind = FakeDocMindClient()
    llm = FakeLLMService()
    app.dependency_overrides[get_docmind_client] = lambda: docmind
    app.dependency_overrides[get_llm_service] = lambda: llm
    yield docmind, llm
    app.dependency_overrides.pop(get_docmind_client, None)
    app.dependency_overrides.pop(get_llm_service, None)


def _generate(client, headers, **payload):
    return client.post(
        "/ai/tasks/from-document",
        headers=headers,
        json={"document_id": 7, **payload},
    )


def test_requires_auth(client):
    response = client.post(
        "/ai/tasks/from-document",
        json={"document_id": 7},
    )

    assert response.status_code == 401


def test_generates_tasks_with_citations(
        client,
        alice_headers,
        fakes,
):
    _, llm = fakes
    llm.specs = [
        GeneratedTaskSpec(
            title="完成登录接口联调",
            description="下周一前与前端联调登录接口。",
            priority="high",
            deadline=date(2026, 11, 2),
            source_chunk_index=0,
        ),
        GeneratedTaskSpec(
            title="补充 token 过期测试",
            description="覆盖 token 过期场景。",
            priority="medium",
            source_chunk_index=5,
        ),
    ]

    response = _generate(
        client,
        alice_headers,
        instruction="只关注测试与联调",
        max_tasks=2,
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["document_id"] == 7
    assert data["filename"] == "plan.pdf"
    assert len(data["tasks"]) == 2
    first = data["tasks"][0]
    assert first["title"] == "完成登录接口联调"
    assert first["source_document_id"] == 7
    assert first["source_filename"] == "plan.pdf"
    assert first["source_chunk_index"] == 0
    assert data["tasks"][1]["source_chunk_index"] == 5

    # 检索必须锁定在指定文档内。
    assert fakes[0].search_calls[0]["document_id"] == 7
    assert fakes[0].search_calls[0]["top_k"] == 20
    # LLM 收到的是文档片段和用户要求。
    assert llm.calls[0]["instruction"] == "只关注测试与联调"
    assert llm.calls[0]["max_tasks"] == 2
    assert llm.calls[0]["filename"] == "plan.pdf"

    # 生成的任务要自动关联同一份文档，便于后续 linked_only 检索。
    for task in data["tasks"]:
        links = client.get(
            f"/tasks/{task['id']}/documents",
            headers=alice_headers,
        )
        assert links.status_code == 200
        assert [item["document_id"] for item in links.json()["items"]] == [7]


def test_document_not_found_returns_404(
        client,
        alice_headers,
        fakes,
):
    fakes[0].document_error = DocMindDocumentNotFoundError("not found")

    response = _generate(client, alice_headers)

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "document not found"
    assert fakes[1].calls == []


def test_document_not_indexed_returns_409(
        client,
        alice_headers,
        fakes,
):
    fakes[0].document = {
        "id": 7,
        "filename": "plan.pdf",
        "status": "processing",
    }

    response = _generate(client, alice_headers)

    assert response.status_code == 409
    assert response.json()["error"]["message"] == (
        "document is not indexed yet"
    )
    assert fakes[0].search_calls == []
    assert fakes[1].calls == []


def test_document_without_chunks_returns_409(
        client,
        alice_headers,
        fakes,
):
    fakes[0].chunks = []

    response = _generate(client, alice_headers)

    assert response.status_code == 409
    assert response.json()["error"]["message"] == (
        "document has no indexed content"
    )
    assert fakes[1].calls == []


def test_empty_specs_returns_empty_tasks(
        client,
        alice_headers,
        fakes,
):
    fakes[1].specs = []

    response = _generate(client, alice_headers)

    assert response.status_code == 200, response.text
    assert response.json()["tasks"] == []


def test_docmind_unavailable_returns_502(
        client,
        alice_headers,
        fakes,
):
    fakes[0].document_error = DocMindUnavailableError("down")

    response = _generate(client, alice_headers)

    assert response.status_code == 502
    assert response.json()["error"]["message"] == (
        "knowledge service unavailable"
    )


def test_llm_structured_output_error_returns_502(
        client,
        alice_headers,
        fakes,
):
    fakes[1].error = LLMStructuredOutputError("bad json")

    response = _generate(client, alice_headers)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == (
        "LLM_STRUCTURED_OUTPUT_ERROR"
    )
