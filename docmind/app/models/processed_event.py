"""DocMind 消费 TaskFlow 事件的幂等标记。"""
from datetime import datetime

from sqlmodel import Field, SQLModel


class ProcessedEvent(SQLModel, table=True):
    __tablename__ = "processed_events"

    event_id: str = Field(primary_key=True, max_length=64)
    processed_at: datetime = Field(default_factory=datetime.utcnow)
