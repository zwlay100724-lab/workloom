"""用户注销：DocMind 清理数据并回写完成事件。"""
from pathlib import Path

from app.models.conversation import Conversation, ConversationMessage
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.outbox_event import OutboxEvent
from app.models.processed_event import ProcessedEvent
from app.services.account_cleanup_service import process_event
from shared.event_queue import Event
from sqlmodel import Session, select

USER_ID = 9


def test_user_deleted_cleans_data_and_emits_completion(
        account_engine,
        tmp_path: Path,
):
    file_path = tmp_path / "meeting.pdf"
    file_path.write_text("content")

    with Session(account_engine) as session:
        doc = Document(
            filename="meeting.pdf",
            file_path=str(file_path),
            user_id=USER_ID,
            status="completed",
        )
        session.add(doc)
        session.flush()
        session.add(DocumentChunk(
            document_id=doc.id,
            user_id=USER_ID,
            content="x",
            chunk_index=0,
        ))
        conversation = Conversation(user_id=USER_ID, title="c")
        session.add(conversation)
        session.flush()
        session.add(ConversationMessage(
            conversation_id=conversation.id,
            role="user",
            content="hello",
        ))
        session.commit()

    event = Event.create(
        event_type="user.deleted",
        aggregate_id=USER_ID,
        user_id=USER_ID,
        payload={"requested_at": "2026-11-11T00:00:00"},
    )
    process_event(event, account_engine)

    with Session(account_engine) as session:
        assert session.exec(select(Document)).all() == []
        assert session.exec(select(DocumentChunk)).all() == []
        assert session.exec(select(Conversation)).all() == []
        assert session.exec(select(ConversationMessage)).all() == []
        assert len(session.exec(select(ProcessedEvent)).all()) == 1
        completion = session.exec(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "user.deleted.completed"
            )
        ).one()
        assert completion.aggregate_id == USER_ID

    assert not file_path.exists()


def test_duplicate_user_deleted_event_is_idempotent(account_engine):
    event = Event.create(
        event_type="user.deleted",
        aggregate_id=USER_ID,
        user_id=USER_ID,
    )
    process_event(event, account_engine)
    process_event(event, account_engine)

    with Session(account_engine) as session:
        assert len(session.exec(select(ProcessedEvent)).all()) == 1
        assert len(session.exec(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "user.deleted.completed"
            )
        ).all()) == 1
