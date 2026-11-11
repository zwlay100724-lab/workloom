"""DocMind 的跨服务事件 outbox。

业务事务和"待发事件"写在同一个数据库事务里，提交后再由 worker 投递。
这样删除 / 重建索引 / 重命名 / 恢复不会出现"业务成功、事件丢失"的双写窗口。
"""
from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class OutboxEvent(SQLModel, table=True):
    __tablename__ = "outbox_events"

    id: int | None = Field(default=None, primary_key=True)
    event_id: str = Field(unique=True, index=True, max_length=64)
    event_type: str = Field(index=True, max_length=64)
    aggregate_id: int = Field(index=True)
    user_id: int = Field(index=True)
    payload: dict = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    published_at: datetime | None = Field(default=None, index=True)
