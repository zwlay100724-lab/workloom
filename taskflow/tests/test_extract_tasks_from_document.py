"""LLMService.extract_tasks_from_document 的单元测试。

直接替换模块级 generate()，不调用真实模型。
覆盖：结构化解析、chunk_index 幻觉过滤、条数截断、校验失败重试。
"""
import asyncio
import json

import pytest
from app.services import llm_service as module
from shared.exceptions import LLMStructuredOutputError


class FakeResult:
    def __init__(self, content: str) -> None:
        self.content = content


def _service() -> module.LLMService:
    # 两个依赖在本测试里都不用：generate() 被整体替换。
    return module.LLMService(
        conversation_service=None,
        session=None,
    )


def _payload(tasks: list[dict]) -> str:
    return json.dumps({"tasks": tasks}, ensure_ascii=False)


def _chunks() -> list[dict]:
    return [
        {"chunk_index": 0, "content": "下周一前完成登录接口联调。"},
        {"chunk_index": 5, "content": "测试用例覆盖 token 过期场景。"},
    ]


def test_parses_and_filters_hallucinated_chunk_index(monkeypatch):
    calls: list[dict] = []

    async def fake_generate(**kwargs):
        calls.append(kwargs)
        return FakeResult(_payload([
            {
                "title": "完成登录接口联调",
                "description": "下周一前完成。",
                "priority": "high",
                "deadline": "2026-11-02",
                "source_chunk_index": 0,
            },
            {
                "title": "补充过期测试",
                "priority": "medium",
                "source_chunk_index": 99,
            },
            {"title": "   ", "priority": "low"},
        ])), None

    monkeypatch.setattr(module, "generate", fake_generate)

    specs = asyncio.run(
        _service().extract_tasks_from_document(
            filename="plan.pdf",
            chunks=_chunks(),
            user_id=1,
            instruction="只关注测试",
            max_tasks=5,
        )
    )

    # 空白标题被丢弃，所以仍然只有两条。
    assert len(specs) == 2
    assert specs[0].title == "完成登录接口联调"
    assert specs[0].source_chunk_index == 0
    # 99 不在本次片段里 → 幻觉引用，置空。
    assert specs[1].source_chunk_index is None
    assert "plan.pdf" in calls[0]["messages"][1]["content"]
    assert "只关注测试" in calls[0]["messages"][1]["content"]


def test_truncates_to_max_tasks(monkeypatch):
    async def fake_generate(**kwargs):
        return FakeResult(_payload([
            {"title": f"任务 {index}", "priority": "low"}
            for index in range(5)
        ])), None

    monkeypatch.setattr(module, "generate", fake_generate)

    specs = asyncio.run(
        _service().extract_tasks_from_document(
            filename="plan.pdf",
            chunks=_chunks(),
            user_id=1,
            max_tasks=2,
        )
    )

    assert [spec.title for spec in specs] == ["任务 0", "任务 1"]


def test_retries_once_then_raises_on_invalid_json(monkeypatch):
    calls: list[dict] = []

    async def fake_generate(**kwargs):
        calls.append(kwargs)
        return FakeResult("not json"), None

    monkeypatch.setattr(module, "generate", fake_generate)

    with pytest.raises(LLMStructuredOutputError):
        asyncio.run(
            _service().extract_tasks_from_document(
                filename="plan.pdf",
                chunks=_chunks(),
                user_id=1,
                max_tasks=3,
            )
        )

    assert len(calls) == 2
    assert calls[0]["response_format"] == {"type": "json_object"}
    # 第二次去掉 response_format 再试一次。
    assert calls[1]["response_format"] is None
