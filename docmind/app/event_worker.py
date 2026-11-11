"""DocMind 的事件 worker：消费 TaskFlow 发来的 user.deleted。

运行：
    cd docmind && ../.venv/bin/python -m app.event_worker

索引 worker 负责重文档任务；这个进程只做轻量的用户数据清理，
避免清理和索引互相阻塞。
"""
import logging
import time

from dotenv import load_dotenv

load_dotenv()

from shared.db.database import engine
from shared.event_queue import EventQueueUnavailableError, RedisEventQueue
from shared.logging import configure_logging

from app.core.config import settings
from app.services.account_cleanup_service import process_event
from app.services.event_publisher import publish_pending_events

logger = logging.getLogger(__name__)


def main() -> None:
    configure_logging()
    queue = RedisEventQueue.from_url(
        settings.EVENT_REDIS_URL or settings.redis_url,
        settings.EVENT_CONSUME_QUEUE_NAME,
    )
    try:
        queue.recover()
    except EventQueueUnavailableError:
        logger.warning("event queue unavailable during recovery", exc_info=True)

    logger.info(
        "DocMind event worker started queue=%s",
        settings.EVENT_CONSUME_QUEUE_NAME,
    )

    while True:
        try:
            # 补投清理完成事件；失败会由外层统一按队列不可用处理。
            publish_pending_events(engine)

            item = queue.dequeue(timeout_seconds=5)
            if item is None:
                continue

            event, raw = item
            try:
                process_event(event, engine)
                queue.ack(raw)
            except Exception:
                queue.ack(raw)
                next_attempt = event.attempt + 1
                if next_attempt < settings.EVENT_MAX_ATTEMPTS:
                    delay = settings.EVENT_RETRY_BASE_DELAY_SECONDS * (
                        2 ** event.attempt
                    )
                    queue.enqueue(event.next_attempt(), delay_seconds=delay)
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
            logger.exception("DocMind event worker loop error")
            time.sleep(1)


if __name__ == "__main__":
    main()
