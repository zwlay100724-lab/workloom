from datetime import datetime

from shared.db.base import Base
from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship


#用 Python 类来描述数据库中的两张核心表：conversations（对话会话表） 和 messages（消息记录表）
class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(#定义主键
        primary_key=True,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),#外键约束：指向 users 表的 id 字段，表示“这个会话属于哪个用户”
        nullable=False,
        index=True,
    )

    title: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    messages: Mapped[list["Message"]] = relationship(#告诉 SQLAlchemy，Conversation 和 Message 是一对多关系
        back_populates="conversation",#双向绑定的名字。在 Message 类里也有一个 conversation 属性，两者通过这个名字互相感知。
        cascade="all, delete-orphan",#极重要的级联设置，如果删除其中一个会话，其下的消息会一起删除
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
    )

    conversation_id: Mapped[int] = mapped_column(
        ForeignKey(
            "conversations.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    conversation: Mapped["Conversation"] = relationship(
        back_populates="messages",
    )