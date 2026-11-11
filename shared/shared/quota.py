"""共享 Redis 月度 token 配额。

TaskFlow 登录时写入用户上限；TaskFlow / DocMind 在调用模型前检查，
调用成功后自增已用量。计数器按自然月 key 存储，月底自动过期。

配额是软限制：调用前只能估算 token，允许少量超用；Redis 故障时
fail-open，避免影响主业务。
"""
import logging
from datetime import datetime, timezone

from redis.exceptions import RedisError

logger = logging.getLogger("shared.quota")

LIMIT_KEY = "quota:limit:{uid}"


def _month() -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m"), now


def used_key(user_id: int) -> str:
    month, _ = _month()
    return f"quota:used:{user_id}:{month}"


def limit_key(user_id: int) -> str:
    return LIMIT_KEY.format(uid=user_id)


def _next_month_epoch(now: datetime) -> int:
    if now.month == 12:
        target = now.replace(
            year=now.year + 1,
            month=1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    else:
        target = now.replace(
            month=now.month + 1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    return int(target.timestamp())


def get_used(redis, user_id: int) -> int:
    return int(redis.get(used_key(user_id)) or 0)


def set_used(redis, user_id: int, tokens: int) -> None:
    _, now = _month()
    redis.set(
        used_key(user_id),
        tokens,
        exat=_next_month_epoch(now),
    )


def set_limit(redis, user_id: int, limit: int) -> None:
    redis.set(limit_key(user_id), limit)


def get_limit(redis, user_id: int, default_limit: int) -> int:
    value = redis.get(limit_key(user_id))
    return int(value) if value is not None else default_limit


def check_quota(
    redis,
    user_id: int,
    estimated_tokens: int,
    default_limit: int,
) -> bool:
    try:
        used = get_used(redis, user_id)
        limit = get_limit(redis, user_id, default_limit)
        return used + max(0, estimated_tokens) <= limit
    except RedisError:
        logger.warning("quota check failed; allowing request", exc_info=True)
        return True


def add_usage(redis, user_id: int, tokens: int) -> None:
    if tokens <= 0:
        return
    try:
        _, now = _month()
        key = used_key(user_id)
        pipe = redis.pipeline()
        pipe.incrby(key, tokens)
        pipe.expireat(key, _next_month_epoch(now))
        pipe.execute()
    except RedisError:
        logger.warning("quota increment failed", exc_info=True)


def month_start_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def seconds_to_month_end() -> int:
    _, now = _month()
    # 多留一天，避免时区边界导致 key 提前过期。
    return max(1, int((datetime.fromtimestamp(
        _next_month_epoch(now),
        timezone.utc,
    ) - now).total_seconds())) + 86400
