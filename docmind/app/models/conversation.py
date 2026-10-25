from datetime import datetime

from sqlmodel import Field, SQLModel


class Conversation(SQLModel, table=True):
    """对话会话：一次多轮问答对应一个 conversation"""
    __tablename__ = "conversations"

    id: int | None = Field(default=None, primary_key=True)
    # 不能写 foreign_key="users.id"：users 表属于 taskflow 自己的库(5432)，
    # docmind 是另一个库(5433)，跨库没有外键（同 documents.user_id 的做法）。
    user_id: int = Field(index=True)
    title: str = Field(default="新对话")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ConversationMessage(SQLModel, table=True):
    """对话消息：每一轮的问题和回答"""
    __tablename__ = "conversation_messages"

    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="conversations.id", index=True)
    role: str = Field()  # "user" 或 "assistant"
    content: str = Field()
    created_at: datetime = Field(default_factory=datetime.utcnow)