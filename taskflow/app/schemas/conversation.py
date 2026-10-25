from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreate(BaseModel):
    title: str | None = Field(
        default=None,
        max_length=200,
    )


class ConversationRead(BaseModel):
    id: int
    title: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True
    )


class MessageRead(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True
    )


class ConversationChatRequest(BaseModel):
    message: str = Field(
        min_length=1,
        max_length=5000,
    )


class ConversationChatResponse(BaseModel):
    conversation_id: int
    answer: str