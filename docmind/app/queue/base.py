"""索引队列的抽象层。

队列负责"任务怎么排队"，worker 负责"任务怎么处理"：
- API 只依赖 IndexQueue 接口，不关心底层是 Redis 还是内存；
- 生产用 Redis 实现，测试用内存实现，worker 逻辑两者都能跑。
"""
import json
from dataclasses import dataclass, replace
from typing import Protocol


class QueueUnavailableError(Exception):
    """队列后端不可达（Redis 连接失败、超时等）。"""


@dataclass(frozen=True)
class IndexJob:
    """一条索引任务。

    attempt 从 0 开始；worker 失败后自增并延迟重投。
    """

    document_id: int
    user_id: int
    attempt: int = 0

    def to_payload(self) -> str:
        return json.dumps(
            {
                "document_id": self.document_id,
                "user_id": self.user_id,
                "attempt": self.attempt,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_payload(cls, raw: str) -> "IndexJob":
        data = json.loads(raw)
        return cls(
            document_id=int(data["document_id"]),
            user_id=int(data["user_id"]),
            attempt=int(data.get("attempt", 0)),
        )

    def next_attempt(self) -> "IndexJob":
        return replace(self, attempt=self.attempt + 1)


class IndexQueue(Protocol):
    def enqueue(self, job: IndexJob, delay_seconds: float = 0) -> None:
        """入队；delay_seconds > 0 时进入延迟队列。"""

    def dequeue(self, timeout_seconds: int = 5) -> IndexJob | None:
        """阻塞取一条任务；超时返回 None。"""

    def promote_due(self) -> int:
        """把到期的延迟任务移回就绪队列，返回移动条数。"""
