"""任务摘要接口与引用校验的测试。"""
import asyncio
import json
from types import SimpleNamespace

import pytest
from app.clients.docmind import DocMindUnavailableError, get_docmind_client
from app.main import app
from app.schemas.knowledge import (
    KnowledgeSummaryCitationSpec,
    TaskKnowledgeSummarySpec,
)
from app.services import llm_service as llm_service_module
from app.services.llm_service import LLMService, get_llm_service
from shared.exceptions import LLMStructuredOutputError


class FakeDocMindClient:
    def __init__(
            self,
            results: list[dict] | None = None,
            error: Exception | None = None,
    ) -> None:
        self.results = results if results is not None else [
            {
                "document_id": 7,
                "filename": "handbook.pdf",
                "chunk_index": 0,
                "page": 3,
                "content": "正式员工每年享有 10 天带薪年假。",
                "score": 0.9,
            },
            {
                "document_id": 7,
                "filename": "handbook.pdf",
                "chunk_index": 5,
                "page": 4,
                "content": "请假需要提前在系统提交申请。",
                "score": 0.8,
            },
        ]
        self.error = error
        self.calls: list[dict] = []

    async def get_document(self, *, document_id: int, token: str) -> dict:
        return {
            "id": document_id,
            "filename": "handbook.pdf",
            "index_version": 1,
            "status": "completed",
        }

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
            "top_k": top_k,
            "document_id": document_id,
            "mode": mode,
        })
        if self.error is not None:
            raise self.error
        return self.results


class FakeLLMService:
    def __init__(
            self,
            summary: TaskKnowledgeSummarySpec | None = None,
            error: Exception | None = None,
    ) -> None:
        self.summary = summary or TaskKnowledgeSummarySpec(
            summary="执行任务前先确认年假额度和申请流程。",
            citations=[
                KnowledgeSummaryCitationSpec(
                    document_id=7,
                    chunk_index=0,
                    text="正式员工每年享有 10 天带薪年假。",
                ),
                KnowledgeSummaryCitationSpec(
                    document_id=7,
                    chunk_index=99,
                    text="不存在的引用。",
                ),
            ],
        )
        self.error = error
        self.calls: list[dict] = []

    async def summarize_task_knowledge(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.summary


@pytest.fixture
def fake_docmind():
    fake = FakeDocMindClient()
    app.dependency_overrides[get_docmind_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_docmind_client, None)


@pytest.fixture
def fake_llm():
    fake = FakeLLMService()
    app.dependency_overrides[get_llm_service] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_llm_service, None)


def _create_task(client, headers) -> int:
    response = client.post(
        "/tasks",
        headers=headers,
        json={
            "title": "申请年假",
            "description": "下周三请假",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_summary_returns_summary_and_valid_citations(
        client,
        alice_headers,
        fake_docmind,
        fake_llm,
):
    task_id = _create_task(client, alice_headers)

    response = client.post(
        f"/tasks/{task_id}/knowledge/summary",
        headers=alice_headers,
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["summary"] == "执行任务前先确认年假额度和申请流程。"
    assert data["mode"] == "hybrid"
    assert len(data["sources"]) == 2
    # 不存在于本次片段里的 chunk 99 被丢弃。
    assert [item["chunk_index"] for item in data["citations"]] == [0]
    assert data["citations"][0]["filename"] == "handbook.pdf"
    assert data["citations"][0]["page"] == 3
    assert fake_llm.calls[0]["title"] == "申请年假"


def test_summary_without_chunks_skips_llm(
        client,
        alice_headers,
        fake_docmind,
        fake_llm,
):
    fake_docmind.results = []
    task_id = _create_task(client, alice_headers)

    response = client.post(
        f"/tasks/{task_id}/knowledge/summary",
        headers=alice_headers,
    )

    assert response.status_code == 200
    assert response.json()["summary"].startswith("资料不足")
    assert response.json()["citations"] == []
    assert fake_llm.calls == []


def test_summary_docmind_unavailable_returns_502(
        client,
        alice_headers,
        fake_docmind,
        fake_llm,
):
    fake_docmind.error = DocMindUnavailableError("down")
    task_id = _create_task(client, alice_headers)

    response = client.post(
        f"/tasks/{task_id}/knowledge/summary",
        headers=alice_headers,
    )

    assert response.status_code == 502
    assert response.json()["error"]["message"] == (
        "knowledge service unavailable"
    )
    assert fake_llm.calls == []


def test_summary_llm_failure_returns_502(
        client,
        alice_headers,
        fake_docmind,
        fake_llm,
):
    fake_llm.error = LLMStructuredOutputError("bad json")
    task_id = _create_task(client, alice_headers)

    response = client.post(
        f"/tasks/{task_id}/knowledge/summary",
        headers=alice_headers,
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == (
        "LLM_STRUCTURED_OUTPUT_ERROR"
    )


def test_summary_linked_only_uses_linked_documents(
        client,
        alice_headers,
        fake_docmind,
        fake_llm,
):
    task_id = _create_task(client, alice_headers)
    linked = client.post(
        f"/tasks/{task_id}/documents",
        headers=alice_headers,
        json={"document_id": 7},
    )
    assert linked.status_code == 201, linked.text

    response = client.post(
        f"/tasks/{task_id}/knowledge/summary",
        headers=alice_headers,
        params={"linked_only": True},
    )

    assert response.status_code == 200, response.text
    assert fake_docmind.calls
    assert all(call["document_id"] == 7 for call in fake_docmind.calls)
    assert all(call["mode"] == "hybrid" for call in fake_docmind.calls)


def test_llm_summary_filters_invalid_citations(monkeypatch):
    payload = {
        "summary": "摘要",
        "citations": [
            {"document_id": 7, "chunk_index": 0, "text": "有效"},
            {"document_id": 7, "chunk_index": 99, "text": "无效"},
        ],
    }

    async def fake_generate(**kwargs):
        return SimpleNamespace(content=json.dumps(payload)), None

    monkeypatch.setattr(llm_service_module, "generate", fake_generate)
    service = LLMService(None, None)

    result = asyncio.run(
        service.summarize_task_knowledge(
            title="申请年假",
            description=None,
            chunks=[
                {
                    "document_id": 7,
                    "chunk_index": 0,
                    "content": "正式员工每年享有 10 天带薪年假。",
                }
            ],
            user_id=1,
        )
    )

    assert [citation.chunk_index for citation in result.citations] == [0]
