from app.models.conversation import Conversation, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class ConversationRepository:
    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        self.session = session

    async def create_conversation(
        self,
        *,
        user_id: int,
        title: str | None,
    ) -> Conversation:

        conversation = Conversation(
            user_id=user_id,
            title=title,
        )
        self.session.add(conversation)
        await self.session.commit()
        await self.session.refresh(conversation)
        return conversation

    async def get_owned_conversation(
            self,
            *,
            conversation_id: int,
            user_id: int,
    ) -> Conversation | None:
        stmt = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def add_message(
            self,
            *,
            conversation_id: int,
            role: str,
            content: str,
    ) -> Message:
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
        )
        self.session.add(message)
        await self.session.commit()
        await self.session.refresh(message)
        return message

    async def list_recent_messages(
            self,
            *,
            conversation_id: int,
            limit: int = 20,
    ) -> list[Message]:
        stmt = (
            select(Message)
            .where(
                Message.conversation_id
                == conversation_id
            )
            .order_by(
                Message.created_at.desc()
            )
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        messages = list(
            result.scalars().all()
        )
        messages.reverse()
        return messages