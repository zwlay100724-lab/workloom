"""TaskFlow → DocMind 的事件 outbox。

用户注销时，用户状态和 user.deleted 事件在同一事务提交；提交后
由事件 worker 投递到 DocMind 队列，失败则补投。
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
