"""公共事件模型的序列化 / 重试字段。"""
from shared.event_queue import Event


def test_event_payload_roundtrip():
    event = Event.create(
        event_type="document.deleted",
        aggregate_id=10,
        user_id=1,
        payload={"deleted_at": "2026-11-11T00:00:00"},
    )

    decoded = Event.from_json(event.to_json())

    assert decoded == event
    assert decoded.next_attempt().attempt == 1
