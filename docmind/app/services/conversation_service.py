from datetime import datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import func
from sqlmodel import Session, select

from app.core.exceptions import ConversationNotFoundError
from app.models.conversation import Conversation, ConversationMessage


class ConversationService:
    def __init__(self, session: Session):
        self.session = session

    def _get_owned_conversation(
        self,
        conversation_id: int,
        user_id: int,
    ) -> Conversation:
        """取会话并校验归属。

        不存在和不属于当前用户统一抛 404，避免暴露资源是否存在。
        所有读写方法都先过这一层，RAGService 调用时也自动受保护。
        """
        conversation = self.session.get(Conversation, conversation_id)
        if conversation is None or conversation.user_id != user_id:
            raise ConversationNotFoundError()
        return conversation

    def create_conversation(
        self,
        user_id: int,
        title: str | None = None,
    ) -> Conversation:
        normalized_title = (title or "").strip() or "新对话"
        conversation = Conversation(
            user_id=user_id,
            title=normalized_title,
        )
        self.session.add(conversation)
        self.session.commit()
        self.session.refresh(conversation)
        return conversation

    def add_message(
        self,
        conversation_id: int,
        user_id: int,
        role: str,
        content: str,
    ) -> ConversationMessage:
        conversation = self._get_owned_conversation(
            conversation_id,
            user_id,
        )
        message = ConversationMessage(
            conversation_id=conversation_id,
            role=role,
            content=content,
        )
        self.session.add(message)
        conversation.updated_at = datetime.utcnow()
        self.session.add(conversation)
        self.session.commit()
        self.session.refresh(message)
        return message

    def get_history(
        self,
        conversation_id: int,
        user_id: int,
        limit: int = 10,
    ) -> list[dict]:
        """获取最近 N 轮历史，返回 LangChain 消息格式。"""
        self._get_owned_conversation(conversation_id, user_id)

        statement = (
            select(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == conversation_id
            )
            .order_by(ConversationMessage.id.desc())
            .limit(limit * 2)  # 每轮2条（user + assistant）
        )
        messages = self.session.exec(statement).all()
        messages = list(reversed(messages))
        return [{"role": m.role, "content": m.content} for m in messages]

    def list_conversations(
        self,
        user_id: int,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[list[Conversation], int]:
        items = self.session.exec(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
            .offset(skip)
            .limit(limit)
        ).all()

        total = self.session.exec(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.user_id == user_id)
        ).one()

        return items, total

    def list_messages(
        self,
        conversation_id: int,
        user_id: int,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[ConversationMessage], int]:
        self._get_owned_conversation(conversation_id, user_id)

        items = self.session.exec(
            select(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == conversation_id
            )
            .order_by(ConversationMessage.id.asc())
            .offset(skip)
            .limit(limit)
        ).all()

        total = self.session.exec(
            select(func.count())
            .select_from(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == conversation_id
            )
        ).one()

        return items, total

    def delete_conversation(
        self,
        conversation_id: int,
        user_id: int,
    ) -> None:
        conversation = self._get_owned_conversation(
            conversation_id,
            user_id,
        )

        # 当前迁移的外键没有 ON DELETE CASCADE，先显式删消息。
        self.session.execute(
            sa_delete(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation_id
            )
        )
        self.session.delete(conversation)
        self.session.commit()
