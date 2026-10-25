"""P1-03：LLMService 结构化输出、工具循环、意图重试的 mock 测试。"""
import asyncio
from types import SimpleNamespace

import pytest
from app.llm.callbacks import UsageTrackingCallbackHandler
from app.llm.client import LLMResult
from app.models.llm_usage import LLMUsage
from app.schemas.ai import TaskIntent
from app.services import llm_service as module
from app.services.llm_service import LLMService
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage
from shared.exceptions import LLMStructuredOutputError
from sqlmodel import Session, select


def _result(content: str) -> LLMResult:
    return LLMResult(
        content=content,
        model="fake-model",
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
    )


class FakeLLMClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def chat(self, messages, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeChain:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    async def ainvoke(self, inputs, config=None):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _service(session, conversation_service=None):
    return LLMService(
        conversation_service=conversation_service or SimpleNamespace(),
        session=session,
    )


# ── 结构化输出：extract_task / plan_tasks ──────────────────────────────
def test_extract_task_retries_without_response_format(
    test_engine,
    monkeypatch,
):
    client = FakeLLMClient(
        [
            _result("not-json"),
            _result(
                '{"title":"写报告","priority":"high",'
                '"deadline":null}'
            ),
        ]
    )
    monkeypatch.setattr(module, "llm_client", client)

    with Session(test_engine) as session:
        data, usage = asyncio.run(
            _service(session).extract_task(
                message="明天写报告",
                user_id=1,
            )
        )
        rows = session.exec(select(LLMUsage)).all()

    assert data.title == "写报告"
    assert data.priority == "high"
    assert usage.total_tokens == 15
    assert len(client.calls) == 2
    assert client.calls[0]["response_format"] == {
        "type": "json_object"
    }
    assert client.calls[1]["response_format"] is None
    assert len(rows) == 2


def test_extract_task_raises_after_two_invalid_outputs(
    test_engine,
    monkeypatch,
):
    client = FakeLLMClient([_result("bad"), _result("still-bad")])
    monkeypatch.setattr(module, "llm_client", client)

    with Session(test_engine) as session:
        with pytest.raises(LLMStructuredOutputError):
            asyncio.run(
                _service(session).extract_task(
                    message="明天写报告",
                    user_id=1,
                )
            )
        rows = session.exec(select(LLMUsage)).all()

    assert len(client.calls) == 2
    # 两次失败也都真实消耗了 token，必须各记一条。
    assert len(rows) == 2


def test_plan_tasks_valid_json(test_engine, monkeypatch):
    payload = (
        '{"goal":"完成报告","tasks":['
        '{"title":"写初稿","priority":"high","reason":"先完成主体"}'
        '],"notes":null}'
    )
    client = FakeLLMClient([_result(payload)])
    monkeypatch.setattr(module, "llm_client", client)

    with Session(test_engine) as session:
        plan = asyncio.run(
            _service(session).plan_tasks(
                message="帮我规划报告",
                user_id=1,
            )
        )

    assert plan.goal == "完成报告"
    assert plan.tasks[0].title == "写初稿"


# ── 意图解析：重试分支 ─────────────────────────────────────────────────
def test_intent_chain_retries_parser_error(
    test_engine,
    monkeypatch,
):
    chain = FakeChain(
        [
            OutputParserException("bad json"),
            TaskIntent(title="写报告"),
        ]
    )
    monkeypatch.setattr(module, "get_task_intent_chain", lambda: chain)

    with Session(test_engine) as session:
        result = asyncio.run(
            _service(session).parse_task_intent(
                user_input="明天写报告",
                user_id=1,
            )
        )

    assert result.title == "写报告"
    assert chain.calls == 2


def test_intent_chain_raises_after_two_failures(
    test_engine,
    monkeypatch,
):
    chain = FakeChain(
        [ValueError("bad"), ValueError("still bad")]
    )
    monkeypatch.setattr(
        module,
        "get_task_intent_structured_chain",
        lambda: chain,
    )

    with Session(test_engine) as session:
        with pytest.raises(LLMStructuredOutputError):
            asyncio.run(
                _service(session).parse_task_intent_structured(
                    user_input="明天写报告",
                    user_id=1,
                )
            )

    assert chain.calls == 2


# ── 工具循环 ───────────────────────────────────────────────────────────
class FakeTool:
    name = "create_task"

    def __init__(self):
        self.args = None

    async def ainvoke(self, args):
        self.args = args
        return {"success": True, "task": {"id": 1}}


class FakeBoundModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def ainvoke(self, messages, config=None):
        self.calls += 1
        return self.responses.pop(0)


class FakeModel:
    def __init__(self, bound):
        self.bound = bound
        self.tools = None

    def bind_tools(self, tools):
        self.tools = tools
        return self.bound


def _tool_call_message() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call-1",
                "name": "create_task",
                "args": {"title": "写报告"},
            }
        ],
    )


def test_chat_with_tools_executes_tool_then_answers(
    test_engine,
    monkeypatch,
):
    tool = FakeTool()
    bound = FakeBoundModel(
        [_tool_call_message(), AIMessage(content="已创建")]
    )
    model = FakeModel(bound)
    monkeypatch.setattr(module, "build_task_tools", lambda **kwargs: [tool])
    monkeypatch.setattr(module, "get_chat_model", lambda: model)

    with Session(test_engine) as session:
        answer = asyncio.run(
            _service(session).chat_with_tools(
                message="帮我创建任务",
                user_id=1,
            )
        )

    assert answer == "已创建"
    assert tool.args == {"title": "写报告"}
    assert bound.calls == 2


def test_chat_with_tools_stops_after_max_rounds(
    test_engine,
    monkeypatch,
):
    tool = FakeTool()
    bound = FakeBoundModel([_tool_call_message(), _tool_call_message()])
    model = FakeModel(bound)
    monkeypatch.setattr(module, "build_task_tools", lambda **kwargs: [tool])
    monkeypatch.setattr(module, "get_chat_model", lambda: model)

    with Session(test_engine) as session:
        with pytest.raises(RuntimeError, match="exceeded 2 rounds"):
            asyncio.run(
                _service(session).chat_with_tools(
                    message="循环",
                    user_id=1,
                    max_rounds=2,
                )
            )

    assert bound.calls == 2


def test_record_handler_usage_writes_all_records(test_engine):
    handler = UsageTrackingCallbackHandler(user_id=1)
    handler.records.extend(
        [
            {
                "user_id": 1,
                "conversation_id": None,
                "model": "fake-model",
                "input_tokens": 3,
                "output_tokens": 2,
                "total_tokens": 5,
                "latency_ms": 10,
            },
            {
                "user_id": 1,
                "conversation_id": None,
                "model": "fake-model",
                "input_tokens": 4,
                "output_tokens": 1,
                "total_tokens": 5,
                "latency_ms": 11,
            },
        ]
    )

    with Session(test_engine) as session:
        asyncio.run(_service(session)._record_handler_usage(handler))
        rows = session.exec(select(LLMUsage)).all()

    assert len(rows) == 2
    assert sum(row.total_tokens for row in rows) == 10


# ── 带历史聊天 ─────────────────────────────────────────────────────────
class FakeConversationService:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    async def get_owned_conversation(self, **kwargs):
        return SimpleNamespace(id=1)

    async def list_recent_messages(
        self,
        *,
        conversation_id,
        limit=20,
    ):
        return []

    async def add_message(self, *, conversation_id, role, content):
        self.messages.append((role, content))


class FakeChatChain:
    async def ainvoke(self, inputs):
        return AIMessage(
            content="你好",
            usage_metadata={
                "input_tokens": 4,
                "output_tokens": 2,
                "total_tokens": 6,
            },
            response_metadata={"model_name": "fake-model"},
        )


def test_chat_with_history_saves_messages_and_usage(
    test_engine,
    monkeypatch,
):
    conversation_service = FakeConversationService()
    monkeypatch.setattr(
        module,
        "get_chat_chain",
        lambda: FakeChatChain(),
    )

    with Session(test_engine) as session:
        answer = asyncio.run(
            _service(session, conversation_service).chat_with_history(
                conversation_id=1,
                user_id=1,
                message="hi",
            )
        )
        rows = session.exec(select(LLMUsage)).all()

    assert answer == "你好"
    assert conversation_service.messages == [
        ("user", "hi"),
        ("assistant", "你好"),
    ]
    assert len(rows) == 1
    assert rows[0].total_tokens == 6
