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
from app.api.ai import BASE_TASK_QUERIES, TASK_QUERY_TOP_K
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
            chunks_by_query: dict[str, list[dict]] | None = None,
            error_by_query: dict[str, Exception] | None = None,
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
        self.chunks_by_query = chunks_by_query
        self.error_by_query = error_by_query
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
            "mode": mode,
        })
        if self.error_by_query and query in self.error_by_query:
            raise self.error_by_query[query]
        if self.search_error is not None:
            raise self.search_error
        if self.chunks_by_query is not None and query in self.chunks_by_query:
            return self.chunks_by_query[query]
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


def test_preview_document_deleted_during_search_returns_404(
        client,
        alice_headers,
        fakes,
):
    # get_document 成功，但检索时文档已被删除。
    fakes[0].search_error = DocMindDocumentNotFoundError("gone")

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


# ── preview 多查询检索 ────────────────────────────────────────────────

def test_preview_runs_base_queries_without_instruction(
        client,
        alice_headers,
        fakes,
):
    fakes[1].specs = []

    response = _preview(client, alice_headers)

    assert response.status_code == 200, response.text
    calls = fakes[0].search_calls
    assert len(calls) == len(BASE_TASK_QUERIES)
    assert {call["query"] for call in calls} == set(BASE_TASK_QUERIES)
    assert all(
        call["top_k"] == TASK_QUERY_TOP_K
        and call["document_id"] == 7
        and call["mode"] == "hybrid"
        for call in calls
    )


def test_preview_appends_instruction_as_extra_query(
        client,
        alice_headers,
        fakes,
):
    fakes[1].specs = []
    instruction = "只关注测试相关任务"

    response = _preview(
        client,
        alice_headers,
        instruction=instruction,
    )

    assert response.status_code == 200, response.text
    calls = fakes[0].search_calls
    assert len(calls) == len(BASE_TASK_QUERIES) + 1
    queries = {call["query"] for call in calls}
    assert set(BASE_TASK_QUERIES) <= queries
    assert instruction in queries
    # instruction 仍然作为 LLM 的提取约束。
    assert fakes[1].calls[0]["instruction"] == instruction


def test_preview_dedupes_chunks_across_queries(
        client,
        alice_headers,
        fakes,
):
    fakes[1].specs = [
        GeneratedTaskSpec(
            title="完成登录接口联调",
            source_chunk_index=0,
        ),
        GeneratedTaskSpec(
            title="补充 token 过期测试",
            source_chunk_index=5,
        ),
    ]

    response = _preview(client, alice_headers)

    assert response.status_code == 200, response.text
    chunks = fakes[1].calls[0]["chunks"]
    # 每一路都返回同样两条，合并后只能出现一次。
    assert [chunk["chunk_index"] for chunk in chunks] == [0, 5]
    assert response.json()["tasks"][0]["source_snippet"] == (
        "下周一前完成登录接口联调。"
    )


def test_preview_survives_one_query_unavailable(
        client,
        alice_headers,
        fakes,
):
    fakes[0].error_by_query = {
        BASE_TASK_QUERIES[0]: DocMindUnavailableError("timeout"),
    }
    fakes[1].specs = []

    response = _preview(client, alice_headers)

    assert response.status_code == 200, response.text
    # 其余几路仍然返回片段，LLM 正常调用。
    assert fakes[1].calls


def test_preview_one_query_empty_still_returns_results(
        client,
        alice_headers,
        fakes,
):
    fakes[0].chunks_by_query = {BASE_TASK_QUERIES[0]: []}
    fakes[1].specs = []

    response = _preview(client, alice_headers)

    assert response.status_code == 200, response.text
    chunks = fakes[1].calls[0]["chunks"]
    assert {chunk["chunk_index"] for chunk in chunks} == {0, 5}


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


# ── 去重 ───────────────────────────────────────────────────────────────

def test_apply_skips_existing_titles(client, alice_headers, fakes):
    first = _apply(
        client,
        alice_headers,
        [
            _spec(title="Write Tests"),
            _spec(title="补充 token 过期测试", source_chunk_index=5),
        ],
    )
    assert first.status_code == 200, first.text
    assert len(first.json()["tasks"]) == 2
    assert first.json()["skipped"] == []

    # 大小写、首尾空格、连续空白都归一化后再比较。
    second = _apply(
        client,
        alice_headers,
        [
            _spec(title="  write   tests "),
            _spec(title="新任务"),
        ],
    )
    assert second.status_code == 200, second.text
    data = second.json()
    assert [task["title"] for task in data["tasks"]] == ["新任务"]
    assert [task["title"] for task in data["skipped"]] == [
        "  write   tests ",
    ]

    tasks = client.get("/tasks", headers=alice_headers).json()
    assert tasks["total"] == 3


def test_apply_dedupes_titles_within_one_request(
        client,
        alice_headers,
        fakes,
):
    response = _apply(
        client,
        alice_headers,
        [
            _spec(title="完成登录接口联调"),
            _spec(title="完成登录接口联调"),
        ],
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["tasks"]) == 1
    assert len(data["skipped"]) == 1


def test_apply_does_not_dedupe_across_documents(
        client,
        alice_headers,
        fakes,
):
    first = _apply(
        client,
        alice_headers,
        [_spec(title="完成登录接口联调")],
        document_id=7,
    )
    second = _apply(
        client,
        alice_headers,
        [_spec(title="完成登录接口联调")],
        document_id=8,
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert len(second.json()["tasks"]) == 1
    assert second.json()["skipped"] == []


def test_apply_does_not_dedupe_manual_task_with_same_title(
        client,
        alice_headers,
        fakes,
):
    created = client.post(
        "/tasks",
        headers=alice_headers,
        json={"title": "完成登录接口联调"},
    )
    assert created.status_code == 201, created.text

    response = _apply(
        client,
        alice_headers,
        [_spec(title="完成登录接口联调")],
    )

    assert response.status_code == 200, response.text
    assert len(response.json()["tasks"]) == 1
    assert response.json()["skipped"] == []


def test_preview_marks_already_exists(
        client,
        alice_headers,
        fakes,
):
    _apply(client, alice_headers, [_spec(title="完成登录接口联调")])

    fakes[1].specs = [
        GeneratedTaskSpec(title="完成登录接口联调"),
        GeneratedTaskSpec(title="全新任务"),
    ]

    response = _preview(client, alice_headers)

    assert response.status_code == 200, response.text
    tasks = response.json()["tasks"]
    assert tasks[0]["already_exists"] is True
    assert tasks[1]["already_exists"] is False
    # preview 仍然不写库。
    assert client.get("/tasks", headers=alice_headers).json()["total"] == 1
