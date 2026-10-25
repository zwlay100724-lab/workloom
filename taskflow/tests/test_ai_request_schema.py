"""P0-01 回归：StructuredTaskRequest 只能有一个定义，字段是 message。

背景：schemas/ai.py 末尾曾重复定义 StructuredTaskRequest(text=...)，
覆盖了前面的 message 版本；三个 AI 接口访问 payload.message 时抛
AttributeError，统一变成 500。这里从 schema 和接口两层把行为钉住。
"""
from app.main import app
from app.schemas.ai import (
    StructuredTaskData,
    StructuredTaskRequest,
    TaskIntent,
)
from app.services.llm_service import get_llm_service


class FakeLLMService:
    """替代真实 LLMService，测试只关心请求/响应契约，不调用外部 API。"""

    async def extract_task(self, *, message: str, user_id: int):
        return StructuredTaskData(title=message, priority="medium"), None

    async def parse_task_intent(self, *, user_input: str, user_id: int):
        return TaskIntent(title=user_input)

    async def parse_task_intent_structured(self, *, user_input: str, user_id: int):
        return TaskIntent(title=user_input)


def test_request_schema_uses_message_only():
    assert "message" in StructuredTaskRequest.model_fields
    assert "text" not in StructuredTaskRequest.model_fields


def test_structured_chat_accepts_message(client, alice_headers):
    app.dependency_overrides[get_llm_service] = lambda: FakeLLMService()

    response = client.post(
        "/ai/chat/structured",
        json={"message": "明天交报告"},
        headers=alice_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["title"] == "明天交报告"


def test_intent_endpoints_accept_message(client, alice_headers):
    app.dependency_overrides[get_llm_service] = lambda: FakeLLMService()

    for path in ("/ai/chat/intent", "/ai/chat/intent/structured"):
        response = client.post(
            path,
            json={"message": "明天交报告"},
            headers=alice_headers,
        )
        assert response.status_code == 200, f"{path}: {response.text}"
        assert response.json()["title"] == "明天交报告"


def test_text_field_is_rejected(client, alice_headers):
    """旧的 text 字段不应再被接受，防止重复定义悄悄回来。"""
    # 这里也要覆盖 LLMService：否则依赖解析会走到真实 get_async_session，
    # 而测试库是 sqlite+pysqlite（没有异步驱动），请求会在校验前就 500。
    app.dependency_overrides[get_llm_service] = lambda: FakeLLMService()

    response = client.post(
        "/ai/chat/structured",
        json={"text": "明天交报告"},
        headers=alice_headers,
    )

    assert response.status_code == 422
