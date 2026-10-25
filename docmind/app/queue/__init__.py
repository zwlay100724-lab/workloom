"""索引队列包。

FastAPI 通过 get_index_queue 依赖拿队列；测试可以 override 成内存实现。
"""
from functools import lru_cache

from app.queue.redis_queue import RedisIndexQueue


@lru_cache(maxsize=1)
def get_index_queue() -> RedisIndexQueue:
    return RedisIndexQueue.from_settings()
