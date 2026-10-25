"""P1-11/P1-12 回归：async LLM 路径的记账不阻塞、流中断也记账。"""
import asyncio
from types import SimpleNamespace

import pytest
from app.models.llm_usage import LLMUsage
from app.services import llm_service as llm_service_module
from app.services.llm_service import LLMService
from langchain_core.messages import AIMessage
from shared.exceptions import LLMServiceUnavailableError
from sqlmodel import Session, select


class FakeChunk:
    def __init__(self, content: str = "", usage_metadata=None):
        self.content = content
        self.usage_metadata = usage_metadata


class FakeChatChain:
    def __init__(self, fail_stream: bool = False):
        self.fail_stream = fail_stream

    async def ainvoke(self, inputs):
        return AIMessage(
            content="你好",
            usage_metadata={
                "input_tokens": 3,
                "output_tokens": 2,
                "total_tokens": 5,
            },
            response_metadata={"model_name": "fake-model"},
        )

    async def astream(self, inputs):
        yield FakeChunk("你")
        if self.fail_stream:
            raise LLMServiceUnavailableError("stream down")
        yield FakeChunk("好")
        yield FakeChunk(
            "",
            {
                "input_tokens": 3,
                "output_tokens": 2,
                "total_tokens": 5,
            },
        )


def _service(session):
    return LLMService(
        conversation_service=SimpleNamespace(),
        session=session,
    )


def test_chat_records_usage(test_engine, monkeypatch):
    monkeypatch.setattr(
        llm_service_module,
        "get_chat_chain",
        lambda: FakeChatChain(),
    )

    with Session(test_engine) as session:
        answer = asyncio.run(
            _service(session).chat(message="hi", user_id=1)
        )
        usages = session.exec(select(LLMUsage)).all()

    assert answer == "你好"
    assert len(usages) == 1
    assert usages[0].total_tokens == 5


def test_stream_chat_records_usage(test_engine, monkeypatch):
    monkeypatch.setattr(
        llm_service_module,
        "get_chat_chain",
        lambda: FakeChatChain(),
    )

    async def collect(service):
        return [
            chunk
            async for chunk in service.stream_chat(
                message="hi",
                user_id=1,
            )
        ]

    with Session(test_engine) as session:
        chunks = asyncio.run(collect(_service(session)))

    with Session(test_engine) as session:
        usages = session.exec(select(LLMUsage)).all()

    assert chunks == ["你", "好"]
    assert len(usages) == 1
    assert usages[0].total_tokens == 5


def test_stream_chat_records_usage_on_error(test_engine, monkeypatch):
    monkeypatch.setattr(
        llm_service_module,
        "get_chat_chain",
        lambda: FakeChatChain(fail_stream=True),
    )

    async def collect(service):
        return [
            chunk
            async for chunk in service.stream_chat(
                message="hi",
                user_id=1,
            )
        ]

    with Session(test_engine) as session:
        with pytest.raises(LLMServiceUnavailableError):
            asyncio.run(collect(_service(session)))

    with Session(test_engine) as session:
        usages = session.exec(select(LLMUsage)).all()

    # 流中断时 usage_metadata 可能为空，但这次调用仍必须落一条记录。
    assert len(usages) == 1
    assert usages[0].total_tokens == 0
