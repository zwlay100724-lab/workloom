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
    # 关联是文档级的：重建后直接升级版本并清掉失效，不需要用户重新关联。
    assert link.invalidated_at is None
    assert link.invalid_reason is None
    assert link.index_version == 2
    # 来源引用是片段级的：旧 chunk_index 可能漂移，等待读路径按 uid 解析。
    assert task.source_invalidated_at is not None
    assert task.source_invalid_reason == "document_reindexed"
    # 版本在解析成功前保持旧值，不能假装已经迁移。
    assert task.source_index_version == 1


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


def test_reindexed_event_does_not_resurrect_deleted_link(test_engine):
    seed(test_engine, index_version=1)
    process_event(
        make_event("document.deleted", {
            "deleted_at": datetime.utcnow().isoformat(),
        }),
        test_engine,
    )

    process_event(
        make_event("document.reindexed", {
            "old_index_version": 1,
            "new_index_version": 2,
            "reindexed_at": datetime.utcnow().isoformat(),
        }),
        test_engine,
    )

    task, link = load(test_engine)
    assert link.invalid_reason == "document_deleted"
    assert task.source_invalid_reason == "document_deleted"
