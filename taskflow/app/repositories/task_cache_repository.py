import json
import logging

from redis.exceptions import RedisError
from shared.db.redis import redis_client

logger = logging.getLogger("taskflow.cache")#日志记录器，专门记录缓存相关的日志，方便和请求日志、业务日志区分开。
TASK_CACHE_TTL = 60
MISSING_TASK_TTL = 10

#构建缓存KEY
def build_task_list_key(
    owner_id: int,
    page: int,
    page_size: int,
    keyword: str | None,
    done: bool | None,
) -> str:
    #参数规范化处理，生成同一个 key，命中同一份缓存，避免因为格式差异导致缓存失效
    normalized_keyword = (
        keyword.strip().lower()
    if keyword
        else ""
    )

    if done is None:
        done_value = "all"
    else:
        done_value = str(done).lower()
#最终KEY格式
    return (
        f"taskflow:tasks:list:"
        f"{owner_id}:"
        f"{page}:"
        f"{page_size}:"
        f"{done_value}:"
        f"{normalized_keyword}"
    )

#读取缓存
def get_task_list_cache(
    owner_id: int,
    page: int,
    page_size: int,
    keyword: str | None,
    done: bool | None,
):

    key = build_task_list_key(
        owner_id=owner_id,
        page=page,
        page_size=page_size,
        keyword=keyword,
        done=done,
    )

    try:
        cached = redis_client.get(key)

    except RedisError:
        logger.exception(
            "Redis GET failed"
        )
        return None

    if cached is None:
        logger.info(
            "CACHE MISS: %s",
            key,
        )
        return None

    logger.info(
        "CACHE HIT: %s",
        key,
    )

    return json.loads(cached)


def set_task_list_cache(
    owner_id: int,
    page: int,
    page_size: int,
    keyword: str | None,
    done: bool | None,
    result: dict,
):

    key = build_task_list_key(
        owner_id=owner_id,
        page=page,
        page_size=page_size,
        keyword=keyword,
        done=done,
    )

    serializable_result = {
        "page": result["page"],
        "page_size": result["page_size"],
        "total": result["total"],
        "tasks": [
            task.model_dump(
                mode="json"
            )
            for task in result["tasks"]
        ],
    }

    try:
        redis_client.setex(
            key,
            TASK_CACHE_TTL,
            json.dumps(
                serializable_result
            ),
        )

    except RedisError:
        logger.exception(
            "Redis SET failed"
        )

def invalidate_task_list_cache(
    owner_id: int,
):

    pattern = (
        f"taskflow:tasks:list:"
        f"{owner_id}:*"
    )

    try:
        keys = list(
            redis_client.scan_iter(
                match=pattern
            )
        )

        if keys:
            redis_client.delete(
                *keys
            )

    except RedisError:
        logger.exception(
            "Redis invalidation failed"
        )

def build_missing_task_key(
    task_id: int,
) -> str:

    return (
        f"taskflow:task:missing:"
        f"{task_id}"
    )


def is_missing_task_cached(
    task_id: int,
) -> bool:

    key = build_missing_task_key(
        task_id
    )

    try:
        return (
            redis_client.get(key)
            is not None
        )

    except RedisError:
        return False


def cache_missing_task(
    task_id: int,
):

    key = build_missing_task_key(
        task_id
    )

    try:
        redis_client.setex(
            key,
            MISSING_TASK_TTL,
            "1",
        )

    except RedisError:
        logger.exception(
            "Negative cache failed"
        )


def clear_missing_task_cache(
    task_id: int,
):

    key = build_missing_task_key(
        task_id
    )

    try:
        redis_client.delete(key)

    except RedisError:
        logger.exception(
            "Negative cache clear failed"
        )