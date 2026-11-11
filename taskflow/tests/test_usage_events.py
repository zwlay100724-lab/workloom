"""DocMind 用量事件落库。"""
from app.models.llm_usage import LLMUsage
from app.models.user import User
from app.services.document_event_service import process_event
from shared.event_queue import Event
from sqlmodel import Session, select


def test_usage_reported_persists_docmind_source(
        client,
        test_engine,
        alice_headers,
):
    # 先注册登录，确保 users 表里有记录。
    client.get("/tasks", headers=alice_headers)
    with Session(test_engine) as session:
        user_id = session.exec(select(User)).one().id

    process_event(
        Event.create(
            event_type="usage.reported",
            aggregate_id=user_id,
            user_id=user_id,
            payload={
                "service": "docmind",
                "operation": "rag",
                "model": "deepseek-v4-pro",
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
                "calls": 2,
                "latency_ms": 900,
                "estimated_cost_usd": "0.0001",
            },
        ),
        test_engine,
    )

    with Session(test_engine) as session:
        usage = session.exec(select(LLMUsage)).one()
        assert usage.user_id == user_id
        assert usage.source == "docmind"
        assert usage.operation == "rag"
        assert usage.total_tokens == 120
