"""P1：重建索引事件 → 旧版本引用标 stale。"""
from datetime import datetime

from app.services.document_event_service import process_event

from document_event_helpers import load, make_event, seed


def test_reindexed_event_marks_old_version_stale(test_engine):
    seed(test_engine, index_version=1)

    process_event(
        make_event("document.reindexed", {
            "old_index_version": 1,
            "new_index_version": 2,
            "reindexed_at": datetime.utcnow().isoformat(),
        }),
        test_engine,
    )

    task, link = load(test_engine)
    assert link.invalid_reason == "document_reindexed"
    assert task.source_invalid_reason == "document_reindexed"


def test_reindexed_event_does_not_touch_current_version(test_engine):
    seed(test_engine, index_version=2)

    process_event(
        make_event("document.reindexed", {
            "old_index_version": 1,
            "new_index_version": 2,
            "reindexed_at": datetime.utcnow().isoformat(),
        }),
        test_engine,
    )

    task, link = load(test_engine)
    assert link.invalidated_at is None
    assert task.source_invalidated_at is None
