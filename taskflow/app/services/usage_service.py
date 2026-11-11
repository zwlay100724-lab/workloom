"""DocMind 用量事件落库。

DocMind 已经在本地把用量加到共享 Redis 配额，因此这里只落库，
不再重复 add_usage。
"""
from decimal import Decimal

from app.models.llm_usage import LLMUsage
from shared.event_queue import Event
from sqlmodel import Session


def handle_usage_reported(session: Session, event: Event) -> None:
    payload = event.payload
    cost = payload.get("estimated_cost_usd")
    session.add(LLMUsage(
        user_id=event.user_id,
        conversation_id=None,
        model=str(payload.get("model") or "unknown"),
        input_tokens=int(payload.get("input_tokens") or 0),
        output_tokens=int(payload.get("output_tokens") or 0),
        total_tokens=int(payload.get("total_tokens") or 0),
        latency_ms=int(payload.get("latency_ms") or 0),
        estimated_cost_usd=(
            Decimal(str(cost)) if cost is not None else None
        ),
        source=str(payload.get("service") or "docmind"),
        operation=str(payload.get("operation") or "unknown"),
    ))
