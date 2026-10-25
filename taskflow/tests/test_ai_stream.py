"""P1-12 回归：流式接口在异常时发送 SSE error，而不是静默截断。"""
from app.main import app
from app.services.llm_service import get_llm_service
from shared.exceptions import LLMServiceUnavailableError


class FailingStreamService:
    async def stream_chat(self, *, message: str, user_id: int):
        raise LLMServiceUnavailableError("stream down")
        yield ""  # pragma: no cover


def test_stream_emits_error_event(client, alice_headers):
    app.dependency_overrides[get_llm_service] = (
        lambda: FailingStreamService()
    )

    with client.stream(
        "POST",
        "/ai/chat/stream",
        json={"message": "hi"},
        headers=alice_headers,
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: error" in body
    assert "LLM_UNAVAILABLE" in body
    # 不把底层异常文本泄漏给客户端。
    assert "stream down" not in body
