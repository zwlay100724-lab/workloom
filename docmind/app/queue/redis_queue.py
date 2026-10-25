"""基于 Redis 的索引队列实现。

结构：
- ready   (list) ：待处理任务，LPUSH 入队、BRPOP 出队（FIFO）
- delayed (zset) ：延迟重试，score 是执行时间戳

为什么用 BRPOP 而不是 BRPOPLPUSH：
这里用"文档状态"兜底丢失任务——worker 崩溃时文档会停在 processing，
重启后的 stale 扫描会重新入队（见 worker.recover_stale）。
这样队列实现保持简单，同时不会永久丢任务。
"""
import logging
import time

import redis

from app.core.config import settings
from app.queue.base import IndexJob, IndexQueue, QueueUnavailableError

logger = logging.getLogger(__name__)


class RedisIndexQueue(IndexQueue):
    def __init__(
            self,
            client: redis.Redis,
            name: str | None = None,
    ) -> None:
        self.client = client
        self.name = name or settings.INDEX_QUEUE_NAME
        self.ready_key = f"{self.name}:ready"
        self.delayed_key = f"{self.name}:delayed"

    @classmethod
    def from_settings(cls) -> "RedisIndexQueue":
        client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
        )
        return cls(client)

    def enqueue(self, job: IndexJob, delay_seconds: float = 0) -> None:
        try:
            if delay_seconds > 0:
                self.client.zadd(
                    self.delayed_key,
                    {job.to_payload(): time.time() + delay_seconds},
                )
            else:
                self.client.lpush(self.ready_key, job.to_payload())
        except redis.RedisError as exc:
            raise QueueUnavailableError(str(exc)) from exc

    def dequeue(self, timeout_seconds: int = 5) -> IndexJob | None:
        try:
            item = self.client.brpop(
                [self.ready_key],
                timeout=timeout_seconds,
            )
        except redis.RedisError as exc:
            raise QueueUnavailableError(str(exc)) from exc

        if item is None:
            return None

        _, raw = item
        try:
            return IndexJob.from_payload(raw)
        except (ValueError, KeyError, TypeError):
            # 坏数据不能卡住队列：丢弃并记日志。
            logger.warning("discarding malformed index job: %r", raw)
            return None

    def promote_due(self) -> int:
        now = time.time()
        try:
            due = self.client.zrangebyscore(
                self.delayed_key,
                0,
                now,
            )
            if not due:
                return 0

            pipeline = self.client.pipeline()
            for raw in due:
                pipeline.zrem(self.delayed_key, raw)
                pipeline.lpush(self.ready_key, raw)
            pipeline.execute()
            return len(due)
        except redis.RedisError as exc:
            raise QueueUnavailableError(str(exc)) from exc
