"""共享状态 Redis（吊销黑名单 / token 版本 / 配额计数器）。

不要复用 TaskFlow 的缓存 Redis：缓存需要 LRU 淘汰，而黑名单和配额
不能被淘汰。调用方通过 shared_redis_url 指向队列型 Redis。
"""
import redis


def make_shared_redis(url: str) -> redis.Redis:
    return redis.Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=1.0,
        socket_timeout=1.0,
    )
