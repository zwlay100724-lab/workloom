"""用户注销 saga 的 TaskFlow 侧。

流程：
TaskFlow 请求注销 → status=deleting + user.deleted 事件
DocMind 清理数据 → user.deleted.completed 事件
TaskFlow 收到完成事件 → 硬删本服务内的用户数据
"""
import logging
from datetime import datetime, timedelta

from app.models.conversation import Conversation, Message
from app.models.llm_usage import LLMUsage
from app.models.task import Task
from app.models.task_document import TaskDocument
from app.models.user import User
from shared.event_queue import Event
from sqlalchemy import delete
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


def handle_user_deleted_completed(session: Session, event: Event) -> None:
    """DocMind 清理完成：按外键顺序删除 TaskFlow 侧数据。"""
    user_id = event.aggregate_id

    task_ids = list(session.exec(
        select(Task.id).where(Task.owner_id == user_id)
    ).all())
    if task_ids:
        session.exec(
            delete(TaskDocument).where(
                TaskDocument.task_id.in_(task_ids)
            )
        )
        session.exec(
            delete(Task).where(Task.id.in_(task_ids))
        )

    conversation_ids = list(session.exec(
        select(Conversation.id).where(Conversation.user_id == user_id)
    ).all())
    if conversation_ids:
        session.exec(
            delete(Message).where(
                Message.conversation_id.in_(conversation_ids)
            )
        )
        session.exec(
            delete(Conversation).where(
                Conversation.id.in_(conversation_ids)
            )
        )

    session.exec(delete(LLMUsage).where(LLMUsage.user_id == user_id))
    session.exec(delete(User).where(User.id == user_id))

    logger.info("hard-deleted TaskFlow user data user_id=%s", user_id)


def list_stuck_deletion_users(
    session: Session,
    stale_minutes: int = 10,
) -> list[int]:
    cutoff = datetime.utcnow() - timedelta(minutes=stale_minutes)
    return list(session.exec(
        select(User.id).where(
            User.status == "deleting",
            User.deleted_at.is_not(None),
            User.deleted_at < cutoff,
        )
    ).all())


def build_user_deleted_event(user_id: int) -> Event:
    return Event.create(
        event_type="user.deleted",
        aggregate_id=user_id,
        user_id=user_id,
        payload={"requested_at": datetime.utcnow().isoformat()},
    )
