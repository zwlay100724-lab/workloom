"""TaskFlow 的共享状态 Redis（吊销 / 配额）。

和任务列表缓存分开：缓存用 REDIS_URL，共享状态用 SHARED_REDIS_URL
（compose 指向 DocMind 的队列 Redis）。未配置时回退到 REDIS_URL。
"""
from functools import lru_cache

from app.core.config import settings
from shared.redis_state import make_shared_redis


@lru_cache(maxsize=1)
def get_shared_redis():
    return make_shared_redis(
        settings.shared_redis_url or settings.redis_url
    )
