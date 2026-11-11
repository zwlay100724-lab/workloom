"""TaskFlow 的跨服务事件 worker。

运行：
    cd taskflow && ../.venv/bin/python -m app.worker

它只消费 DocMind 发来的文档生命周期事件：
删除 / 重建索引 / 重命名 / 恢复。事件处理失败按指数退避重试，
最终失败只记日志（读路径还有 404 懒修复兜底）。
"""
import logging
import time

from dotenv import load_dotenv

# engine / Settings 在 import 时创建，必须先加载 .env。
load_dotenv()

from shared.db.database import engine
from shared.event_queue import EventQueueUnavailableError, RedisEventQueue
from shared.logging import configure_logging

from app.core.config import settings
from app.services.document_event_service import process_event

logger = logging.getLogger(__name__)


def main() -> None:
    configure_logging()
    queue = RedisEventQueue.from_url(
        settings.doc_event_redis_url,
        settings.doc_event_queue_name,
    )
    try:
        queue.recover()
    except EventQueueUnavailableError:
        # Redis 可能还没就绪；主循环会继续重试。
        logger.warning("event queue unavailable during recovery", exc_info=True)
    logger.info(
        "TaskFlow event worker started queue=%s",
        settings.doc_event_queue_name,
    )

    while True:
        try:
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
                if next_attempt < settings.doc_event_max_attempts:
                    delay = settings.doc_event_retry_base_delay * (
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
            logger.exception("event worker loop error")
            time.sleep(1)


if __name__ == "__main__":
    main()
