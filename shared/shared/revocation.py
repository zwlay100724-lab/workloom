"""JWT 吊销状态。

- 单次登录：jti 黑名单，TTL 到 token 自身过期；
- 用户级：ver 版本号，注销/改密时递增，所有旧 token 失效。

这些 key 放在共享 Redis；TaskFlow 写、DocMind 读。
"""
from datetime import datetime, timezone

JTI_KEY = "auth:revoked:jti:{jti}"
VER_KEY = "auth:user:ver:{uid}"


def revoke_jti(redis, jti: str, expires_at: datetime) -> None:
    ttl = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    redis.setex(
        JTI_KEY.format(jti=jti),
        max(1, ttl),
        "1",
    )


def is_jti_revoked(redis, jti: str) -> bool:
    return redis.exists(JTI_KEY.format(jti=jti)) == 1


def set_token_version(redis, user_id: int, version: int) -> None:
    # 不设 TTL：注销后至少保留到旧 token 全部过期；数量很小。
    redis.set(VER_KEY.format(uid=user_id), version)


def get_token_version(redis, user_id: int) -> int | None:
    value = redis.get(VER_KEY.format(uid=user_id))
    return int(value) if value is not None else None
