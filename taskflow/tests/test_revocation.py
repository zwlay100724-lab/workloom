"""共享吊销状态：jti 黑名单和用户 token 版本。"""
from datetime import datetime, timedelta, timezone

from shared.revocation import (
    get_token_version,
    is_jti_revoked,
    revoke_jti,
    set_token_version,
)


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.ttl = {}

    def setex(self, key, ttl, value):
        self.data[key] = value
        self.ttl[key] = ttl

    def exists(self, key):
        return 1 if key in self.data else 0

    def set(self, key, value):
        self.data[key] = value

    def get(self, key):
        return self.data.get(key)


def test_jti_blacklist_has_token_ttl():
    redis = FakeRedis()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    revoke_jti(redis, "jti-1", expires_at)

    assert is_jti_revoked(redis, "jti-1") is True
    assert 0 < redis.ttl["auth:revoked:jti:jti-1"] <= 600


def test_token_version_roundtrip():
    redis = FakeRedis()

    set_token_version(redis, 7, 3)

    assert get_token_version(redis, 7) == 3
    assert get_token_version(redis, 8) is None
