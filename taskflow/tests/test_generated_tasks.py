"""「文档 → 任务」两步流程的回归测试。

preview：只生成建议，不写库，任务附带引用片段摘要；
apply：把用户确认 / 编辑后的任务落库，并自动关联文档。

DocMind 和 LLM 都用 fake 覆盖，验证：
- 文档归属 / 索引状态的前置校验；
- 预览不产生任何数据库写入；
- 应用阶段以用户提交内容为准，来源字段由服务端强制写入；
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

LONG_CONTENT = "长" * 300


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


def _preview(client, headers, **payload):
    return client.post(
        "/ai/tasks/from-document/preview",
        headers=headers,
        json={"document_id": 7, **payload},
    )


def _apply(client, headers, tasks, document_id: int = 7):
    return client.post(
        "/ai/tasks/from-document/apply",
        headers=headers,
        json={"document_id": document_id, "tasks": tasks},
    )


def _spec(**overrides) -> dict:
    payload = {
        "title": "完成登录接口联调",
        "description": "下周一前完成。",
        "priority": "high",
        "deadline": "2026-11-02",
        "source_chunk_index": 0,
    }
    payload.update(overrides)
    return payload


# ── preview ────────────────────────────────────────────────────────────

def test_preview_requires_auth(client):
    response = client.post(
        "/ai/tasks/from-document/preview",
        json={"document_id": 7},
    )

    assert response.status_code == 401


def test_preview_returns_suggestions_without_writing(
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
            priority="medium",
            source_chunk_index=5,
        ),
    ]

    response = _preview(
        client,
        alice_headers,
        instruction="只关注测试与联调",
        max_tasks=2,
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["document_id"] == 7
    assert data["filename"] == "plan.pdf"
    assert data["instruction"] == "只关注测试与联调"
    assert len(data["tasks"]) == 2
    assert data["tasks"][0]["source_chunk_index"] == 0
    # 引用摘要让用户在确认前看到依据。
    assert data["tasks"][0]["source_snippet"] == "下周一前完成登录接口联调。"
    assert data["tasks"][1]["source_snippet"] == (
        "测试用例需要覆盖 token 过期场景。"
    )

    # 检索锁定在指定文档；LLM 收到用户要求。
    assert fakes[0].search_calls[0]["document_id"] == 7
    assert llm.calls[0]["instruction"] == "只关注测试与联调"

    # 关键：preview 不写库。
    tasks = client.get("/tasks", headers=alice_headers)
    assert tasks.json()["total"] == 0


def test_preview_truncates_snippet(client, alice_headers, fakes):
    fakes[0].chunks = [{
        "document_id": 7,
        "filename": "plan.pdf",
        "chunk_index": 0,
        "page": 1,
        "content": LONG_CONTENT,
        "score": 0.9,
    }]
    fakes[1].specs = [
        GeneratedTaskSpec(title="读长片段", source_chunk_index=0)
    ]

    response = _preview(client, alice_headers)

    assert response.status_code == 200, response.text
    assert len(response.json()["tasks"][0]["source_snippet"]) == 200


def test_preview_unknown_chunk_index_has_no_snippet(
        client,
        alice_headers,
        fakes,
):
    # 模拟模型给出一个本次片段里不存在的编号（真实链路会被置空，
    # 这里直接验证接口层的兜底：查不到片段就不返回摘要）。
    fakes[1].specs = [
        GeneratedTaskSpec(title="引用不存在", source_chunk_index=99)
    ]

    response = _preview(client, alice_headers)

    assert response.status_code == 200
    assert response.json()["tasks"][0]["source_snippet"] is None


def test_preview_document_not_found_returns_404(
        client,
        alice_headers,
        fakes,
):
    fakes[0].document_error = DocMindDocumentNotFoundError("not found")

    response = _preview(client, alice_headers)

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "document not found"
    assert fakes[1].calls == []


def test_preview_document_not_indexed_returns_409(
        client,
        alice_headers,
        fakes,
):
    fakes[0].document = {
        "id": 7,
        "filename": "plan.pdf",
        "status": "processing",
    }

    response = _preview(client, alice_headers)

    assert response.status_code == 409
    assert response.json()["error"]["message"] == (
        "document is not indexed yet"
    )
    assert fakes[0].search_calls == []
    assert fakes[1].calls == []


def test_preview_document_without_chunks_returns_409(
        client,
        alice_headers,
        fakes,
):
    fakes[0].chunks = []

    response = _preview(client, alice_headers)

    assert response.status_code == 409
    assert response.json()["error"]["message"] == (
        "document has no indexed content"
    )
    assert fakes[1].calls == []


def test_preview_empty_specs_returns_empty_tasks(
        client,
        alice_headers,
        fakes,
):
    fakes[1].specs = []

    response = _preview(client, alice_headers)

    assert response.status_code == 200, response.text
    assert response.json()["tasks"] == []


def test_preview_docmind_unavailable_returns_502(
        client,
        alice_headers,
        fakes,
):
    fakes[0].document_error = DocMindUnavailableError("down")

    response = _preview(client, alice_headers)

    assert response.status_code == 502
    assert response.json()["error"]["message"] == (
        "knowledge service unavailable"
    )


def test_preview_llm_structured_output_error_returns_502(
        client,
        alice_headers,
        fakes,
):
    fakes[1].error = LLMStructuredOutputError("bad json")

    response = _preview(client, alice_headers)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == (
        "LLM_STRUCTURED_OUTPUT_ERROR"
    )


# ── apply ──────────────────────────────────────────────────────────────

def test_apply_requires_auth(client):
    response = client.post(
        "/ai/tasks/from-document/apply",
        json={"document_id": 7, "tasks": [_spec()]},
    )

    assert response.status_code == 401


def test_apply_creates_tasks_with_citations(
        client,
        alice_headers,
        fakes,
):
    response = _apply(
        client,
        alice_headers,
        [
            _spec(title="用户改过的标题", priority="low"),
            _spec(
                title="补充 token 过期测试",
                source_chunk_index=5,
            ),
        ],
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["document_id"] == 7
    assert data["filename"] == "plan.pdf"
    assert len(data["tasks"]) == 2
    first = data["tasks"][0]
    # 以用户确认后的内容为准。
    assert first["title"] == "用户改过的标题"
    assert first["priority"] == "low"
    # 来源字段由服务端强制写入。
    assert first["source_document_id"] == 7
    assert first["source_filename"] == "plan.pdf"
    assert first["source_chunk_index"] == 0
    assert data["tasks"][1]["source_chunk_index"] == 5

    # 任务自动关联文档，便于后续 linked_only 检索。
    for task in data["tasks"]:
        links = client.get(
            f"/tasks/{task['id']}/documents",
            headers=alice_headers,
        )
        assert links.status_code == 200
        assert [item["document_id"] for item in links.json()["items"]] == [7]

    # apply 不再调用 LLM。
    assert fakes[1].calls == []


def test_apply_rejects_empty_task_list(client, alice_headers, fakes):
    response = _apply(client, alice_headers, [])

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_apply_document_not_found_returns_404(
        client,
        alice_headers,
        fakes,
):
    fakes[0].document_error = DocMindDocumentNotFoundError("not found")

    response = _apply(client, alice_headers, [_spec()])

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "document not found"


def test_apply_document_not_indexed_returns_409(
        client,
        alice_headers,
        fakes,
):
    fakes[0].document = {
        "id": 7,
        "filename": "plan.pdf",
        "status": "failed",
    }

    response = _apply(client, alice_headers, [_spec()])

    assert response.status_code == 409
    assert response.json()["error"]["message"] == (
        "document is not indexed yet"
    )


def test_apply_docmind_unavailable_returns_502(
        client,
        alice_headers,
        fakes,
):
    fakes[0].document_error = DocMindUnavailableError("down")

    response = _apply(client, alice_headers, [_spec()])

    assert response.status_code == 502
    assert response.json()["error"]["message"] == (
        "knowledge service unavailable"
    )
