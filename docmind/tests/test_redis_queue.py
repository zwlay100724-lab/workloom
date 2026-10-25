"""RedisIndexQueue 的单元测试。

用最小的 FakeRedis 验证键名、编码、FIFO 顺序、延迟队列和错误包装，
不需要真实 Redis 实例。
"""
import json
import time

import pytest
import redis
from app.queue.base import IndexJob, QueueUnavailableError
from app.queue.redis_queue import RedisIndexQueue


class FakePipeline:
    def __init__(self, client: "FakeRedis") -> None:
        self.client = client
        self.operations: list[tuple] = []

    def zrem(self, key, member):
        self.operations.append(("zrem", key, member))
        return self

    def lpush(self, key, value):
        self.operations.append(("lpush", key, value))
        return self

    def execute(self):
        for operation in self.operations:
            getattr(self.client, operation[0])(*operation[1:])
        self.operations.clear()


class FakeRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise redis.RedisError("boom")

    def lpush(self, key: str, value: str) -> None:
        self._check()
        self.lists.setdefault(key, []).insert(0, value)

    def brpop(self, keys, timeout: int):
        self._check()
        for key in keys:
            items = self.lists.get(key, [])
            if items:
                return key, items.pop()
        return None

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self._check()
        self.zsets.setdefault(key, {}).update(mapping)

    def zrangebyscore(self, key: str, minimum, maximum):
        self._check()
        return [
            member
            for member, score in sorted(
                self.zsets.get(key, {}).items(),
                key=lambda item: item[1],
            )
            if minimum <= score <= maximum
        ]

    def zrem(self, key: str, member: str) -> None:
        self._check()
        self.zsets.get(key, {}).pop(member, None)

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)


@pytest.fixture
def client():
    return FakeRedis()


@pytest.fixture
def queue(client):
    return RedisIndexQueue(client, name="test:index")


def test_enqueue_and_dequeue_are_fifo(queue):
    queue.enqueue(IndexJob(document_id=1, user_id=10))
    queue.enqueue(IndexJob(document_id=2, user_id=10))

    first = queue.dequeue(timeout_seconds=0)
    second = queue.dequeue(timeout_seconds=0)

    assert first.document_id == 1
    assert second.document_id == 2
    assert queue.dequeue(timeout_seconds=0) is None


def test_delayed_job_waits_until_due(queue, monkeypatch):
    job = IndexJob(document_id=1, user_id=10)
    queue.enqueue(job, delay_seconds=30)

    assert queue.promote_due() == 0
    assert queue.dequeue(timeout_seconds=0) is None

    # 把当前时间推后 60 秒，延迟任务到期。
    # 先保存原函数：redis_queue.time 就是全局 time 模块，直接调用会被自己递归。
    original_time = time.time
    monkeypatch.setattr(
        "app.queue.redis_queue.time.time",
        lambda: original_time() + 60,
    )

    assert queue.promote_due() == 1
    assert queue.dequeue(timeout_seconds=0) == job


def test_malformed_job_is_discarded(queue, client):
    client.lpush(queue.ready_key, "not json")

    assert queue.dequeue(timeout_seconds=0) is None


def test_queue_unavailable_is_wrapped(queue, client):
    client.fail = True

    with pytest.raises(QueueUnavailableError):
        queue.enqueue(IndexJob(document_id=1, user_id=1))
    with pytest.raises(QueueUnavailableError):
        queue.dequeue(timeout_seconds=0)
    with pytest.raises(QueueUnavailableError):
        queue.promote_due()


def test_from_settings_sets_connection_timeouts(monkeypatch):
    captured = {}

    def fake_from_url(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeRedis()

    monkeypatch.setattr(
        "app.queue.redis_queue.redis.Redis.from_url",
        fake_from_url,
    )

    RedisIndexQueue.from_settings()

    # Redis 不可达时必须快速失败，上传才能及时降级。
    assert captured["socket_connect_timeout"] == 2.0
    assert captured["socket_timeout"] == 2.0
    assert captured["decode_responses"] is True


def test_payload_contains_user_and_attempt(queue, client):
    queue.enqueue(IndexJob(document_id=7, user_id=3, attempt=1))

    raw = client.lists[queue.ready_key][0]

    assert json.loads(raw) == {
        "document_id": 7,
        "user_id": 3,
        "attempt": 1,
    }
