"""跨服务事件队列。

DocMind 把文档生命周期事件（删除 / 重建索引 / 重命名 / 恢复）投递给 TaskFlow。
这里只做"至少一次投递"的传输层，不关心业务：

- ready      (list) ：待消费
- processing (list) ：已取出、尚未确认；成功才删除
- delayed    (zset) ：失败后延迟重试

worker 崩溃时，processing 里的事件会留在 Redis；下一次启动调用 recover()
放回 ready，避免事件丢失。消费端必须自己保证幂等。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

import redis
from redis.exceptions import RedisError

logger = logging.getLogger("shared.events")


class EventQueueUnavailableError(Exception):
    """队列后端不可达（Redis 连接失败、超时等）。"""


@dataclass(frozen=True)
class Event:
    """一条跨服务事件。

    payload 只放 ID / 时间 / 版本等小字段，不放文档正文。
    """

    event_id: str
    event_type: str
    aggregate_id: int
    user_id: int
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    schema_version: int = 1
    attempt: int = 0

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "event_id": self.event_id,
                "event_type": self.event_type,
                "aggregate_id": self.aggregate_id,
                "user_id": self.user_id,
                "payload": self.payload,
                "occurred_at": self.occurred_at,
                "attempt": self.attempt,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, raw: str) -> "Event":
        data = json.loads(raw)
        return cls(
            event_id=str(data["event_id"]),
            event_type=str(data["event_type"]),
            aggregate_id=int(data["aggregate_id"]),
            user_id=int(data["user_id"]),
            payload=dict(data.get("payload") or {}),
            occurred_at=str(data.get("occurred_at") or ""),
            schema_version=int(data.get("schema_version", 1)),
            attempt=int(data.get("attempt", 0)),
        )

    @classmethod
    def create(
        cls,
        *,
        event_type: str,
        aggregate_id: int,
        user_id: int,
        payload: dict[str, Any] | None = None,
    ) -> "Event":
        return cls(
            event_id=uuid.uuid4().hex,
            event_type=event_type,
            aggregate_id=aggregate_id,
            user_id=user_id,
            payload=payload or {},
        )

    def next_attempt(self) -> "Event":
        return replace(self, attempt=self.attempt + 1)


class RedisEventQueue:
    """基于 Redis list + zset 的轻量事件队列。

    单 worker 假设：recover() 会把 processing 里的事件全部放回 ready。
    如果以后多 worker 并行消费，需要改成按 worker 分开 processing key，
    或者换 Redis Streams consumer group。
    """

    def __init__(self, client: redis.Redis, name: str) -> None:
        self.client = client
        self.name = name
        self.ready_key = f"{name}:ready"
        self.processing_key = f"{name}:processing"
        self.delayed_key = f"{name}:delayed"

    @classmethod
    def from_url(cls, url: str, name: str) -> "RedisEventQueue":
        client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=2.0,
            socket_timeout=5.0,
        )
        return cls(client, name)

    def enqueue(self, event: Event, delay_seconds: float = 0) -> None:
        try:
            if delay_seconds > 0:
                self.client.zadd(
                    self.delayed_key,
                    {event.to_json(): time.time() + delay_seconds},
                )
            else:
                self.client.lpush(self.ready_key, event.to_json())
        except RedisError as exc:
            raise EventQueueUnavailableError(str(exc)) from exc

    def dequeue(
        self,
        timeout_seconds: int = 5,
    ) -> tuple[Event, str] | None:
        """阻塞取一条事件，返回 (event, raw)；超时返回 None。

        raw 必须交给 ack()：成功删除 processing 里的副本，失败时由
        nack() 删除并延迟重投。这样 worker 崩溃不会丢处理中的事件。
        """
        self.promote_due()

        try:
            raw = self.client.brpoplpush(
                self.ready_key,
                self.processing_key,
                timeout_seconds,
            )
        except RedisError as exc:
            raise EventQueueUnavailableError(str(exc)) from exc

        if raw is None:
            return None

        try:
            return Event.from_json(raw), raw
        except (ValueError, KeyError, TypeError):
            # 坏数据不能卡住队列：从 processing 移除并丢弃。
            logger.warning("discarding malformed event: %r", raw)
            self.ack(raw)
            return None

    def ack(self, raw: str) -> None:
        try:
            self.client.lrem(self.processing_key, 1, raw)
        except RedisError as exc:
            raise EventQueueUnavailableError(str(exc)) from exc

    def nack(self, event: Event, raw: str, delay_seconds: float) -> None:
        """失败后确认旧副本，再按退避延迟投递下一次尝试。"""
        self.ack(raw)
        self.enqueue(event.next_attempt(), delay_seconds=delay_seconds)

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
        except RedisError as exc:
            raise EventQueueUnavailableError(str(exc)) from exc

    def recover(self) -> int:
        """worker 启动时把 processing 中残留的事件放回 ready。

        单 worker 假设：这些事件属于上一次崩溃的同一个 worker。
        """
        recovered = 0
        try:
            while True:
                raw = self.client.rpoplpush(
                    self.processing_key,
                    self.ready_key,
                )
                if raw is None:
                    break
                recovered += 1
        except RedisError as exc:
            raise EventQueueUnavailableError(str(exc)) from exc

        if recovered:
            logger.warning("recovered %s in-flight events", recovered)
        return recovered
