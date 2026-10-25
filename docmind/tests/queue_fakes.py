"""测试用的内存队列。

实现和 RedisIndexQueue 相同的接口，但不需要真实 Redis；
延迟队列不按时间判断，promote_due 直接把所有延迟任务移回就绪队列，
测试通过 delayed 里记录的值断言退避时长。
"""
from app.queue.base import IndexJob, QueueUnavailableError


class MemoryIndexQueue:
    def __init__(self) -> None:
        self.ready: list[IndexJob] = []
        self.delayed: list[tuple[float, IndexJob]] = []
        self.unavailable = False

    def enqueue(self, job: IndexJob, delay_seconds: float = 0) -> None:
        if self.unavailable:
            raise QueueUnavailableError("redis down")
        if delay_seconds > 0:
            self.delayed.append((delay_seconds, job))
        else:
            self.ready.append(job)

    def dequeue(self, timeout_seconds: int = 5) -> IndexJob | None:
        if self.unavailable:
            raise QueueUnavailableError("redis down")
        return self.ready.pop(0) if self.ready else None

    def promote_due(self) -> int:
        moved = len(self.delayed)
        for _, job in self.delayed:
            self.ready.append(job)
        self.delayed.clear()
        return moved
