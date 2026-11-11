"""DocMind 索引 worker 入口。

运行：
    cd docmind && ../.venv/bin/python -m app.worker
或 compose 里的 worker-docmind 服务。

必须在 load_dotenv() 之后再 import app/shared：
engine 和 Settings 都在 import 时创建，需要环境变量先就位。
"""
import logging

from dotenv import load_dotenv

load_dotenv()

from shared.db.database import engine
from shared.logging import configure_logging

from app.queue.redis_queue import RedisIndexQueue
from app.queue.worker import IndexWorker
from app.services.event_publisher import publish_pending_events

logger = logging.getLogger(__name__)


def main() -> None:
    configure_logging()
    queue = RedisIndexQueue.from_settings()
    worker = IndexWorker(
        queue=queue,
        bind=engine,
        # 删除 / 重建索引 / 重命名 / 恢复在提交后会先尝试投递；
        # 这里每 60 秒补投一次未发布事件。
        outbox_publisher=lambda: publish_pending_events(engine),
    )
    logger.info("DocMind index worker starting")
    worker.run_forever()


if __name__ == "__main__":
    main()
