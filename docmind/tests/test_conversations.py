"""P1-01 回归：会话的所有读写都必须校验归属。"""
import pytest
from app.core.exceptions import ConversationNotFoundError
from app.models.conversation import Conversation, ConversationMessage
from app.services.conversation_service import ConversationService
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel


@pytest.fixture
def session():
    # 只建会话相关的两张表；DocumentChunk 的 pgvector 类型不能在 SQLite 上建。
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[Conversation.__table__, ConversationMessage.__table__],
    )
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_create_default_title(session):
    service = ConversationService(session)

    assert service.create_conversation(1).title == "新对话"
    assert service.create_conversation(1, "   ").title == "新对话"
    assert service.create_conversation(1, "  年报  ").title == "年报"


def test_owner_can_add_and_read_messages(session):
    service = ConversationService(session)
    conversation = service.create_conversation(1, "测试")

    service.add_message(conversation.id, 1, "user", "你好")
    service.add_message(conversation.id, 1, "assistant", "你好，有什么可以帮你？")

    history = service.get_history(conversation.id, 1)
    assert history == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好，有什么可以帮你？"},
    ]

    items, total = service.list_messages(conversation.id, 1)
    assert total == 2
    assert [item.role for item in items] == ["user", "assistant"]


def test_other_user_cannot_read_or_write(session):
    service = ConversationService(session)
    conversation = service.create_conversation(1, "测试")

    with pytest.raises(ConversationNotFoundError):
        service.get_history(conversation.id, 2)

    with pytest.raises(ConversationNotFoundError):
        service.add_message(conversation.id, 2, "user", "越权写入")

    with pytest.raises(ConversationNotFoundError):
        service.list_messages(conversation.id, 2)

    # 越权失败后不应留下任何消息
    assert session.exec(select(ConversationMessage)).all() == []


def test_other_user_cannot_delete(session):
    service = ConversationService(session)
    conversation = service.create_conversation(1, "测试")

    with pytest.raises(ConversationNotFoundError):
        service.delete_conversation(conversation.id, 2)

    assert session.get(Conversation, conversation.id) is not None


def test_list_conversations_filters_by_user(session):
    service = ConversationService(session)
    service.create_conversation(1, "alice")
    service.create_conversation(2, "bob")

    items, total = service.list_conversations(1)

    assert total == 1
    assert [item.title for item in items] == ["alice"]


def test_delete_removes_conversation_and_messages(session):
    service = ConversationService(session)
    conversation = service.create_conversation(1, "测试")
    service.add_message(conversation.id, 1, "user", "hi")

    service.delete_conversation(conversation.id, 1)

    assert session.get(Conversation, conversation.id) is None
    assert session.exec(select(ConversationMessage)).all() == []
