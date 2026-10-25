from typing import Annotated

from app.models.conversation import Conversation, Message
from app.repositories.conversation_repository import (
    ConversationRepository,
)
from fastapi import Depends, HTTPException, status
from shared.db.database import get_async_session
from sqlalchemy.ext.asyncio import AsyncSession


class ConversationService:

    def __init__(
        self,
        repository: ConversationRepository,
    ) -> None:
        self.repository = repository

    async def create(
        self,
        *,
        user_id: int,
        title: str | None,
    ) -> Conversation:
        return await self.repository.create_conversation(
            user_id=user_id,
            title=title,
        )

    async def get_owned_conversation(
        self,
        *,
        conversation_id: int,
        user_id: int,
    ) -> Conversation:
        conversation = (
            await self.repository
            .get_owned_conversation(
                conversation_id=conversation_id,
                user_id=user_id,
            )
        )

        if conversation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )

        return conversation

    async def add_message(
        self,
        *,
        conversation_id: int,
        role: str,
        content: str,
    ) -> Message:
        return await self.repository.add_message(
            conversation_id=conversation_id,
            role=role,
            content=content,
        )

    async def list_recent_messages(
        self,
        *,
        conversation_id: int,
        limit: int = 20,
    ) -> list[Message]:
        return await self.repository.list_recent_messages(
            conversation_id=conversation_id,
            limit=limit,
        )


def get_conversation_service(
    session: Annotated[
        AsyncSession,
        Depends(get_async_session),
    ],
) -> ConversationService:
    return ConversationService(
        ConversationRepository(session),
    )

ConversationServiceDep = Annotated[
    ConversationService,
    Depends(get_conversation_service),
]
