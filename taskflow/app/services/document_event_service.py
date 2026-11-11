"""TaskFlow 消费 DocMind 的文档生命周期事件。

四个事件：
- document.deleted   文档软删除 → 关联/来源标失效
- document.reindexed 重建索引 → 旧版本引用标 stale
- document.renamed   重命名 → 刷新文件名快照
- document.restored  恢复完成 → 清除失效并更新版本

一致性策略：
- processed_events 去重（同一 event_id 只处理一次）；
- last_event_at 防乱序（旧删除事件不能覆盖新恢复事件）；
- index_version 判断引用是否基于旧版切分。
"""
import logging
from datetime import datetime, timezone

from app.models.processed_event import ProcessedEvent
from app.models.task import Task
from app.models.task_document import TaskDocument
from shared.event_queue import Event
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

logger = logging.getLogger(__name__)


def _parse_event_time(raw: str | None, fallback: str | None = None) -> datetime:
    value = raw or fallback
    if not value:
        return datetime.utcnow()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _event_time(event: Event, payload_key: str) -> datetime:
    return _parse_event_time(
        event.payload.get(payload_key),
        event.occurred_at,
    )


def _linked_task_ids(user_id: int):
    """TaskDocument 没有 user_id，用任务的 owner_id 过滤用户。"""
    return select(Task.id).where(Task.owner_id == user_id)


def handle_document_deleted(session: Session, event: Event) -> None:
    deleted_at = _event_time(event, "deleted_at")

    session.execute(
        update(TaskDocument)
        .where(
            TaskDocument.document_id == event.aggregate_id,
            TaskDocument.task_id.in_(_linked_task_ids(event.user_id)),
            or_(
                TaskDocument.last_event_at.is_(None),
                TaskDocument.last_event_at <= deleted_at,
            ),
        )
        .values(
            invalidated_at=deleted_at,
            invalid_reason="document_deleted",
            last_event_at=deleted_at,
        )
    )

    session.execute(
        update(Task)
        .where(
            Task.source_document_id == event.aggregate_id,
            Task.owner_id == event.user_id,
            or_(
                Task.source_last_event_at.is_(None),
                Task.source_last_event_at <= deleted_at,
            ),
        )
        .values(
            source_invalidated_at=deleted_at,
            source_invalid_reason="document_deleted",
            source_last_event_at=deleted_at,
        )
    )


def handle_document_reindexed(session: Session, event: Event) -> None:
    new_version = int(event.payload["new_index_version"])
    event_time = _event_time(event, "reindexed_at")

    session.execute(
        update(TaskDocument)
        .where(
            TaskDocument.document_id == event.aggregate_id,
            TaskDocument.task_id.in_(_linked_task_ids(event.user_id)),
            func.coalesce(TaskDocument.index_version, 0) < new_version,
            or_(
                TaskDocument.last_event_at.is_(None),
                TaskDocument.last_event_at <= event_time,
            ),
        )
        .values(
            invalidated_at=event_time,
            invalid_reason="document_reindexed",
            last_event_at=event_time,
        )
    )

    session.execute(
        update(Task)
        .where(
            Task.source_document_id == event.aggregate_id,
            Task.owner_id == event.user_id,
            func.coalesce(Task.source_index_version, 0) < new_version,
            or_(
                Task.source_last_event_at.is_(None),
                Task.source_last_event_at <= event_time,
            ),
        )
        .values(
            source_invalidated_at=event_time,
            source_invalid_reason="document_reindexed",
            source_last_event_at=event_time,
        )
    )


def handle_document_renamed(session: Session, event: Event) -> None:
    filename = event.payload.get("filename")
    if not filename:
        return

    # 重命名只改展示快照，不参与删除/恢复/重建索引的状态先后判断，
    # 所以不更新 last_event_at —— 否则一个较新的 rename 会把一个
    # 更早发生、但更晚到达的 reindex 事件挡掉，导致引用没被标 stale。
    session.execute(
        update(TaskDocument)
        .where(
            TaskDocument.document_id == event.aggregate_id,
            TaskDocument.task_id.in_(_linked_task_ids(event.user_id)),
        )
        .values(filename=filename)
    )

    session.execute(
        update(Task)
        .where(
            Task.source_document_id == event.aggregate_id,
            Task.owner_id == event.user_id,
        )
        .values(source_filename=filename)
    )


def handle_document_restored(session: Session, event: Event) -> None:
    version = int(event.payload["index_version"])
    restored_at = _event_time(event, "restored_at")

    session.execute(
        update(TaskDocument)
        .where(
            TaskDocument.document_id == event.aggregate_id,
            TaskDocument.task_id.in_(_linked_task_ids(event.user_id)),
            or_(
                TaskDocument.last_event_at.is_(None),
                TaskDocument.last_event_at <= restored_at,
            ),
        )
        .values(
            invalidated_at=None,
            invalid_reason=None,
            index_version=version,
            last_event_at=restored_at,
        )
    )

    session.execute(
        update(Task)
        .where(
            Task.source_document_id == event.aggregate_id,
            Task.owner_id == event.user_id,
            or_(
                Task.source_last_event_at.is_(None),
                Task.source_last_event_at <= restored_at,
            ),
        )
        .values(
            source_invalidated_at=None,
            source_invalid_reason=None,
            source_index_version=version,
            source_last_event_at=restored_at,
        )
    )


_HANDLERS = {
    "document.deleted": handle_document_deleted,
    "document.reindexed": handle_document_reindexed,
    "document.renamed": handle_document_renamed,
    "document.restored": handle_document_restored,
}


def dispatch(session: Session, event: Event) -> None:
    handler = _HANDLERS.get(event.event_type)
    if handler is None:
        # 未知事件不阻塞队列：记日志并视为已处理。
        logger.warning(
            "ignoring unknown event type=%s event_id=%s",
            event.event_type,
            event.event_id,
        )
        return
    handler(session, event)


def handle_event(session: Session, event: Event) -> bool:
    """在一个事务里做幂等标记 + 业务更新。

    返回 False 表示重复事件，调用方 ack 即可。
    业务异常会抛出，调用方 rollback 后重试；标记随事务一起回滚。
    """
    session.add(ProcessedEvent(event_id=event.event_id))
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return False

    dispatch(session, event)
    return True


def process_event(event: Event, bind) -> None:
    """worker 入口：独立事务，成功提交，失败回滚交给队列重试。"""
    with Session(bind) as session:
        try:
            if not handle_event(session, event):
                return
            session.commit()
        except Exception:
            session.rollback()
            raise
