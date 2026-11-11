"""用户注销 saga 的 DocMind 侧。

消费 user.deleted：
1. 删除该用户的片段、文档、会话、消息；
2. 同一事务写 user.deleted.completed outbox；
3. 提交后删除物理文件。

清理是幂等的：重复执行删不到数据也仍然发完成事件。
"""
import logging
import os

from shared.event_queue import Event
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models.conversation import Conversation, ConversationMessage
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.processed_event import ProcessedEvent
from app.services.event_publisher import (
    add_outbox_event,
    safe_publish_pending_events,
)

logger = logging.getLogger(__name__)


def cleanup_user(session: Session, user_id: int) -> dict:
    documents = list(session.exec(
        select(Document).where(Document.user_id == user_id)
    ).all())
    file_paths = [doc.file_path for doc in documents]

    session.exec(
        delete(DocumentChunk).where(DocumentChunk.user_id == user_id)
    )
    session.exec(
        delete(Document).where(Document.user_id == user_id)
    )

    conversation_ids = list(session.exec(
        select(Conversation.id).where(Conversation.user_id == user_id)
    ).all())
    if conversation_ids:
        session.exec(
            delete(ConversationMessage).where(
                ConversationMessage.conversation_id.in_(conversation_ids)
            )
        )
        session.exec(
            delete(Conversation).where(
                Conversation.id.in_(conversation_ids)
            )
        )

    return {
        "documents": len(documents),
        "conversations": len(conversation_ids),
        "file_paths": file_paths,
    }


def _claim_event(session: Session, event_id: str) -> bool:
    session.add(ProcessedEvent(event_id=event_id))
    try:
        session.flush()
        return True
    except IntegrityError:
        session.rollback()
        return False


def process_event(event: Event, bind) -> None:
    if event.event_type != "user.deleted":
        logger.warning(
            "ignoring unknown DocMind event type=%s event_id=%s",
            event.event_type,
            event.event_id,
        )
        return

    with Session(bind) as session:
        try:
            if not _claim_event(session, event.event_id):
                return

            result = cleanup_user(session, event.user_id)

            add_outbox_event(
                session,
                event_type="user.deleted.completed",
                aggregate_id=event.user_id,
                user_id=event.user_id,
                payload={
                    "documents": result["documents"],
                    "conversations": result["conversations"],
                },
            )
            session.commit()
        except Exception:
            session.rollback()
            raise

    # 文件删除放在数据库提交之后：最多留下孤儿文件，不会出现记录还在但文件没了。
    for path in result["file_paths"]:
        try:
            os.remove(path)
        except OSError:
            logger.warning(
                "failed to remove user file user_id=%s path=%s",
                event.user_id,
                path,
            )

    safe_publish_pending_events(bind)
    logger.info(
        "cleaned DocMind user data user_id=%s docs=%s conversations=%s",
        event.user_id,
        result["documents"],
        result["conversations"],
    )
