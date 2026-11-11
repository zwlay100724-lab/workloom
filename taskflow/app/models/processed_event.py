"""TaskFlow 消费跨服务事件的幂等标记。

handler 在同一个数据库事务里先插入 event_id，再执行业务更新：
- 重复事件：主键冲突，直接跳过；
- 业务失败：整个事务回滚，标记也不留，事件重试还能重新执行。
"""
from datetime import datetime

from sqlmodel import Field, SQLModel


class ProcessedEvent(SQLModel, table=True):
    __tablename__ = "processed_events"

    event_id: str = Field(primary_key=True, max_length=64)
    processed_at: datetime = Field(default_factory=datetime.utcnow)
