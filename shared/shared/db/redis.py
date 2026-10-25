"""Redis 客户端(带短超时,缓存类服务,宁可快速失败也不拖垮请求)。"""
import os

from redis import Redis

REDIS_URL = os.getenv(
    "REDIS_URL",
    "redis://localhost:6379/0",
)


redis_client = Redis.from_url(
    REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=1,
)