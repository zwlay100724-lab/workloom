"""SQLAlchemy DeclarativeBase。

【为什么 metadata 要和 SQLModel 共享同一份?】
TaskFlow 是"SQLModel + 纯 SQLAlchemy"混用:Task/User 是 SQLModel(table=True),
Conversation/Message 是普通 SQLAlchemy 模型。若两者各用一份 metadata,
Conversation 的 ForeignKey("users.id") 就解析不到 SQLModel 里的 users 表。
把 Base.metadata 指向 SQLModel.metadata,create_all 才能一次建齐所有表。
"""
from sqlalchemy.orm import DeclarativeBase
from sqlmodel import SQLModel


class Base(DeclarativeBase):
    metadata = SQLModel.metadata