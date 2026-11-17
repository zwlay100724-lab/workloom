"""任务「相关知识」的短 TTL 缓存。

检索结果依赖任务内容（标题 + 描述）、检索模式、top_k、关联文档及其
index_version。key 把这些都编码进去；任务更新、关联变化、文档事件
到达时主动失效，60 秒 TTL 兜底。

Redis 不可用时全部 fail-open：缓存只是加速，不能让知识检索失败。
"""
import hashlib
import json
import logging

from redis.exceptions import RedisError
from shared.db.redis import redis_client

logger = logging.getLogger("taskflow.knowledge_cache")

KNOWLEDGE_CACHE_TTL = 60
_KEY_PREFIX = "taskflow:knowledge"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _query_hash(title: str | None, description: str | None) -> str:
    return _hash(f"{title or ''}\n{description or ''}")


def _versions_hash(versions: list[tuple] | None) -> str:
    if not versions:
        return "all"
    raw = "|".join(
        f"{document_id}:{index_version}:{invalidated_at or ''}"
        for document_id, index_version, invalidated_at in sorted(versions)
    )
    return _hash(raw)


def build_key(
        *,
        user_id: int,
        task_id: int,
        linked_only: bool,
        mode: str,
        top_k: int,
        title: str | None,
        description: str | None,
        versions: list[tuple] | None = None,
) -> str:
    """versions 是 [(document_id, index_version, invalidated_at), ...]。"""
    return (
        f"{_KEY_PREFIX}:{user_id}:{task_id}:"
        f"{int(linked_only)}:{mode}:{top_k}:"
        f"{_query_hash(title, description)}:"
        f"{_versions_hash(versions)}"
    )


def get_cached(key: str) -> list[dict] | None:
    try:
        raw = redis_client.get(key)
    except RedisError:
        logger.exception("knowledge cache GET failed")
        return None
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        logger.warning("knowledge cache value is not JSON key=%s", key)
        return None
    return value if isinstance(value, list) else None


def set_cached(key: str, results: list[dict]) -> None:
    try:
        redis_client.setex(
            key,
            KNOWLEDGE_CACHE_TTL,
            json.dumps(results, ensure_ascii=False),
        )
    except RedisError:
        logger.exception("knowledge cache SET failed")


def _delete_pattern(pattern: str) -> None:
    try:
        keys = list(redis_client.scan_iter(match=pattern))
        if keys:
            redis_client.delete(*keys)
    except RedisError:
        logger.exception(
            "knowledge cache invalidation failed pattern=%s",
            pattern,
        )


def invalidate_task(*, user_id: int, task_id: int) -> None:
    _delete_pattern(f"{_KEY_PREFIX}:{user_id}:{task_id}:*")


def invalidate_user(user_id: int) -> None:
    _delete_pattern(f"{_KEY_PREFIX}:{user_id}:*")
