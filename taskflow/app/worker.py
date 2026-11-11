"""TaskFlow 的跨服务事件 worker。

职责：
1. 发布 TaskFlow outbox（用户注销 user.deleted）到 DocMind 队列；
2. 重复投递卡住的注销；
3. 消费 DocMind 事件（文档生命周期、注销完成、用量上报）。

运行：
    cd taskflow && ../.venv/bin/python -m app.worker
"""
import logging
import time

from dotenv import load_dotenv

# engine / Settings 在 import 时创建，必须先加载 .env。
load_dotenv()

from shared.db.database import engine
from shared.event_queue import EventQueueUnavailableError, RedisEventQueue
from shared.logging import configure_logging
from sqlmodel import Session

from app.core.config import settings
from app.services.account_service import (
    build_user_deleted_event,
    list_stuck_deletion_users,
)
from app.services.document_event_service import process_event
from app.services.outbox_publisher import publish_pending_events

logger = logging.getLogger(__name__)


def requeue_stuck_deletions(publisher) -> int:
    with Session(engine) as session:
        user_ids = list_stuck_deletion_users(
            session,
            stale_minutes=settings.account_deletion_stale_minutes,
        )

    for user_id in user_ids:
        publisher.enqueue(build_user_deleted_event(user_id))

    if user_ids:
        logger.warning(
            "re-enqueued stuck account deletions count=%s",
            len(user_ids),
        )
    return len(user_ids)


def main() -> None:
    configure_logging()
    consumer = RedisEventQueue.from_url(
        settings.doc_event_redis_url,
        settings.doc_event_queue_name,
    )
    publisher = RedisEventQueue.from_url(
        settings.doc_event_redis_url,
        settings.doc_event_publish_queue_name,
    )
    try:
        consumer.recover()
    except EventQueueUnavailableError:
        logger.warning("event queue unavailable during recovery", exc_info=True)
    logger.info(
        "TaskFlow event worker started consume=%s publish=%s",
        settings.doc_event_queue_name,
        settings.doc_event_publish_queue_name,
    )

    last_sweep = 0.0
    while True:
        try:
            publish_pending_events(engine, publisher)
            now = time.monotonic()
            if now - last_sweep >= 60:
                requeue_stuck_deletions(publisher)
                last_sweep = now

            item = consumer.dequeue(timeout_seconds=5)
            if item is None:
                continue

            event, raw = item
            try:
                process_event(event, engine)
                consumer.ack(raw)
            except Exception:
                consumer.ack(raw)
                next_attempt = event.attempt + 1
                if next_attempt < settings.doc_event_max_attempts:
                    delay = settings.doc_event_retry_base_delay * (
                        2 ** event.attempt
                    )
                    consumer.enqueue(
                        event.next_attempt(),
                        delay_seconds=delay,
                    )
                    logger.warning(
                        "event failed, retrying event_id=%s type=%s "
                        "attempt=%s delay=%.1fs",
                        event.event_id,
                        event.event_type,
                        next_attempt,
                        delay,
                    )
                else:
                    logger.exception(
                        "event failed permanently event_id=%s type=%s",
                        event.event_id,
                        event.event_type,
                    )
        except EventQueueUnavailableError:
            logger.warning(
                "event queue unavailable; retrying in 5s",
                exc_info=True,
            )
            time.sleep(5)
        except Exception:
            logger.exception("event worker loop error")
            time.sleep(1)


if __name__ == "__main__":
    main()
