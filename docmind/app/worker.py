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

logger = logging.getLogger(__name__)


def main() -> None:
    configure_logging()
    queue = RedisIndexQueue.from_settings()
    worker = IndexWorker(queue=queue, bind=engine)
    logger.info("DocMind index worker starting")
    worker.run_forever()


if __name__ == "__main__":
    main()
