"""DocMind 的共享状态 Redis（吊销 / 配额）。

默认复用 DocMind 的队列 Redis；可用 SHARED_REDIS_URL 覆盖。
"""
from functools import lru_cache

from shared.redis_state import make_shared_redis

from app.core.config import settings


@lru_cache(maxsize=1)
def get_shared_redis():
    return make_shared_redis(
        settings.shared_redis_url or settings.redis_url
    )
