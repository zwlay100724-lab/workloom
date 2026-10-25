"""P1-10 回归：provider 不返回 usage 时不能写 NULL。"""
from app.services.llm_service import _record_usage
from sqlmodel import Session


def test_missing_usage_is_stored_as_zero(test_engine):
    with Session(test_engine) as session:
        usage = _record_usage(
            session=session,
            user_id=1,
            conversation_id=None,
            model="deepseek-v4-pro",
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            latency_ms=12,
        )

    assert usage.input_tokens == 0
    assert usage.output_tokens == 0
    assert usage.total_tokens == 0
    assert usage.estimated_cost_usd is None


def test_partial_usage_fills_total_from_input_and_output(test_engine):
    with Session(test_engine) as session:
        usage = _record_usage(
            session=session,
            user_id=1,
            conversation_id=None,
            model="deepseek-v4-pro",
            input_tokens=10,
            output_tokens=5,
            total_tokens=None,
            latency_ms=20,
        )

    assert usage.input_tokens == 10
    assert usage.output_tokens == 5
    assert usage.total_tokens == 15
