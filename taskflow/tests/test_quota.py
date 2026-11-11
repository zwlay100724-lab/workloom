"""共享配额计数器的基本行为。"""
from shared.quota import (
    add_usage,
    check_quota,
    get_used,
    set_limit,
    used_key,
)


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.operations = []

    def incrby(self, key, amount):
        self.operations.append(("incrby", key, amount))
        return self

    def expireat(self, key, timestamp):
        self.operations.append(("expireat", key, timestamp))
        return self

    def execute(self):
        for op, key, value in self.operations:
            if op == "incrby":
                self.redis.data[key] = int(self.redis.data.get(key, 0)) + value
        self.operations = []


class FakeRedis:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, **kwargs):
        self.data[key] = value

    def pipeline(self):
        return FakePipeline(self)


def test_quota_check_and_increment():
    redis = FakeRedis()
    set_limit(redis, 1, 100)

    assert check_quota(redis, 1, 80, 1000) is True
    add_usage(redis, 1, 80)

    assert get_used(redis, 1) == 80
    assert check_quota(redis, 1, 30, 1000) is False
    assert check_quota(redis, 1, 20, 1000) is True
    assert used_key(1) in redis.data


def test_quota_uses_default_limit_when_redis_has_no_limit():
    redis = FakeRedis()
    assert check_quota(redis, 2, 1000, 1000) is True
    assert check_quota(redis, 2, 1001, 1000) is False
