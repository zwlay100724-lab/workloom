"""DocMind → TaskFlow 的事件投递。

业务代码只负责在事务里 add_outbox_event()；事务提交后调用
safe_publish_pending_events() 做一次 best-effort 投递。
失败不影响主业务，DocMind 的索引 worker 会定期补投未发布事件。
"""
import logging
import uuid
from datetime import datetime

from shared.event_queue import (
    Event,
    EventQueueUnavailableError,
    RedisEventQueue,
)
from sqlmodel import Session, select

from app.core.config import settings
from app.models.outbox_event import OutboxEvent

logger = logging.getLogger(__name__)

_queue: RedisEventQueue | None = None


def get_event_queue() -> RedisEventQueue:
    global _queue
    if _queue is None:
        _queue = RedisEventQueue.from_url(
            settings.EVENT_REDIS_URL or settings.redis_url,
            settings.EVENT_QUEUE_NAME,
        )
    return _queue


def add_outbox_event(
    session: Session,
    *,
    event_type: str,
    aggregate_id: int,
    user_id: int,
    payload: dict,
) -> OutboxEvent:
    """在调用方当前的业务事务里写一条待发事件（不 commit）。"""
    event = OutboxEvent(
        event_id=uuid.uuid4().hex,
        event_type=event_type,
        aggregate_id=aggregate_id,
        user_id=user_id,
        payload=payload,
    )
    session.add(event)
    return event


def publish_pending_events(bind, limit: int = 100) -> int:
    """把未发布事件投递到队列，成功后在同一个 session 里标记 published。

    投递成功但标记提交失败时，下一轮会重复投递；TaskFlow 用
    processed_events 去重，所以允许这种 at-least-once 行为。
    """
    queue = get_event_queue()
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
        logger.info("published %s document events", published)
    return published


def safe_publish_pending_events(bind) -> None:
    """best-effort 投递；失败只记日志，留给 worker 补投。"""
    try:
        publish_pending_events(bind)
    except EventQueueUnavailableError:
        logger.warning(
            "event queue unavailable; pending events will be retried by worker",
            exc_info=True,
        )
    except Exception:
        logger.exception("failed to publish pending events")
