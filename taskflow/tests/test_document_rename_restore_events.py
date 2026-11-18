"""P2：重命名快照、恢复清除失效、旧删除事件乱序保护。"""
from datetime import datetime, timedelta

from app.services.document_event_service import process_event
from document_event_helpers import load, make_event, seed


def test_renamed_event_updates_filename_snapshots(test_engine):
    seed(test_engine)

    process_event(
        make_event("document.renamed", {
            "filename": "会议纪要-v2.pdf",
            "renamed_at": datetime.utcnow().isoformat(),
        }),
        test_engine,
    )

    task, link = load(test_engine)
    assert link.filename == "会议纪要-v2.pdf"
    assert task.source_filename == "会议纪要-v2.pdf"


def test_restore_event_clears_link_and_marks_source_for_resolution(
        test_engine,
):
    seed(test_engine, index_version=2)
    deleted_at = datetime.utcnow()
    process_event(
        make_event("document.deleted", {
            "deleted_at": deleted_at.isoformat(),
        }),
        test_engine,
    )

    restored_at = deleted_at + timedelta(seconds=1)
    process_event(
        make_event("document.restored", {
            "restored_at": restored_at.isoformat(),
            "index_version": 3,
        }),
        test_engine,
    )

    task, link = load(test_engine)
    # 关联是文档级的：恢复后立即有效。
    assert link.invalidated_at is None
    assert link.index_version == 3
    # 来源引用是片段级的：恢复也重建了片段，不能直接宣布旧下标有效。
    assert task.source_invalidated_at is not None
    assert task.source_invalid_reason == "document_reindexed"
    assert task.source_index_version == 2


def test_old_delete_event_cannot_override_restore(test_engine):
    seed(test_engine, index_version=2)
    deleted_at = datetime.utcnow()
    process_event(
        make_event("document.deleted", {
            "deleted_at": deleted_at.isoformat(),
        }),
        test_engine,
    )

    restored_at = deleted_at + timedelta(seconds=1)
    process_event(
        make_event("document.restored", {
            "restored_at": restored_at.isoformat(),
            "index_version": 3,
        }),
        test_engine,
    )

    # 迟到的旧删除事件：payload 里的业务时间早于 restore，必须被忽略。
    process_event(
        make_event(
            "document.deleted",
            {"deleted_at": deleted_at.isoformat()},
            event_id="late-delete",
            occurred_at=restored_at + timedelta(seconds=5),
        ),
        test_engine,
    )

    _, link = load(test_engine)
    assert link.invalidated_at is None
