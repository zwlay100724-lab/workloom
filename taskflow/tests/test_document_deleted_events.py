"""P0：文档删除事件 → 关联/来源失效；重复事件幂等；重新关联恢复。"""
from datetime import datetime

from app.models.processed_event import ProcessedEvent
from app.services import task_service
from app.services.document_event_service import process_event
from sqlmodel import Session, select

from document_event_helpers import DOC_ID, USER_ID, load, make_event, seed


def test_deleted_event_invalidates_link_and_task_source(test_engine):
    seed(test_engine)
    deleted_at = datetime.utcnow()

    process_event(
        make_event("document.deleted", {
            "deleted_at": deleted_at.isoformat(),
        }),
        test_engine,
    )

    task, link = load(test_engine)
    assert link.invalidated_at is not None
    assert link.invalid_reason == "document_deleted"
    assert task.source_invalidated_at is not None
    assert task.source_invalid_reason == "document_deleted"


def test_duplicate_event_is_processed_once(test_engine):
    seed(test_engine)
    event = make_event("document.deleted", {
        "deleted_at": datetime.utcnow().isoformat(),
    })

    process_event(event, test_engine)
    process_event(event, test_engine)

    with Session(test_engine) as session:
        assert len(session.exec(select(ProcessedEvent)).all()) == 1


def test_relink_clears_invalidation_and_updates_version(test_engine):
    task_id = seed(test_engine, index_version=1)
    process_event(
        make_event("document.deleted", {
            "deleted_at": datetime.utcnow().isoformat(),
        }),
        test_engine,
    )

    with Session(test_engine) as session:
        task_service.link_document(
            session=session,
            task_id=task_id,
            owner_id=USER_ID,
            document_id=DOC_ID,
            filename="plan.pdf",
            index_version=2,
        )

    _, link = load(test_engine)
    assert link.invalidated_at is None
    assert link.invalid_reason is None
    assert link.index_version == 2
