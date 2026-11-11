"""TaskFlow 的 outbox 投递。

用户注销等跨服务写操作在业务事务里写 outbox；worker 把未发布事件
投到 DocMind 队列。投递成功但标记失败时允许重复，DocMind 的
processed_events 去重。
"""
import logging
import uuid
from datetime import datetime

from app.models.outbox_event import OutboxEvent
from shared.event_queue import Event, EventQueueUnavailableError
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


def add_outbox_event(
    session: Session,
    *,
    event_type: str,
    aggregate_id: int,
    user_id: int,
    payload: dict,
) -> OutboxEvent:
    row = OutboxEvent(
        event_id=uuid.uuid4().hex,
        event_type=event_type,
        aggregate_id=aggregate_id,
        user_id=user_id,
        payload=payload,
    )
    session.add(row)
    return row


def publish_pending_events(
    bind,
    queue,
    limit: int = 100,
) -> int:
    published = 0
    with Session(bind) as session:
        rows = session.exec(
            select(OutboxEvent)
            .where(OutboxEvent.published_at.is_(None))
            .order_by(OutboxEvent.id.asc())
            .limit(limit)
        ).all()

        for row in rows:
            queue.enqueue(
                Event(
                    event_id=row.event_id,
                    event_type=row.event_type,
                    aggregate_id=row.aggregate_id,
                    user_id=row.user_id,
                    payload=row.payload,
                    occurred_at=row.created_at.isoformat(),
                )
            )
            row.published_at = datetime.utcnow()
            session.add(row)
            published += 1

        session.commit()

    if published:
        logger.info("published %s taskflow events", published)
    return published


def safe_publish_pending_events(bind, queue) -> None:
    try:
        publish_pending_events(bind, queue)
    except EventQueueUnavailableError:
        logger.warning(
            "event queue unavailable; pending events will be retried",
            exc_info=True,
        )
    except Exception:
        logger.exception("failed to publish taskflow events")
