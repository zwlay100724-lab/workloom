"""用户注销：立即吊销、事件落库、完成后硬删。"""
from app.models.conversation import Conversation, Message
from app.models.llm_usage import LLMUsage
from app.models.outbox_event import OutboxEvent
from app.models.task import Task
from app.models.task_document import TaskDocument
from app.models.user import User
from app.services.document_event_service import process_event
from shared.event_queue import Event
from sqlmodel import Session, select


def test_account_delete_revokes_and_emits_event(
        client,
        test_engine,
        alice_headers,
):
    client.post(
        "/tasks",
        json={"title": "注销前任务"},
        headers=alice_headers,
    )

    with Session(test_engine) as session:
        user = session.exec(select(User)).one()
        user_id = user.id

    response = client.post(
        "/account/delete",
        json={"password": "FastAPI123"},
        headers=alice_headers,
    )
    assert response.status_code == 202

    # 旧 token 立即失效（TaskFlow 查数据库状态和版本）。
    assert client.get("/tasks", headers=alice_headers).status_code == 401

    with Session(test_engine) as session:
        user = session.get(User, user_id)
        assert user.status == "deleting"
        assert user.token_version == 1
        event = session.exec(select(OutboxEvent)).one()
        assert event.event_type == "user.deleted"


def test_user_deleted_completed_hard_deletes_data(
        client,
        test_engine,
        alice_headers,
):
    client.post(
        "/tasks",
        json={"title": "待删除任务"},
        headers=alice_headers,
    )

    with Session(test_engine) as session:
        user = session.exec(select(User)).one()
        user_id = user.id
        conversation = Conversation(user_id=user_id, title="c")
        session.add(conversation)
        session.flush()
        session.add(Message(
            conversation_id=conversation.id,
            role="user",
            content="hello",
        ))
        session.add(LLMUsage(
            user_id=user_id,
            model="m",
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
            latency_ms=10,
        ))
        session.commit()

    process_event(
        Event.create(
            event_type="user.deleted.completed",
            aggregate_id=user_id,
            user_id=user_id,
            payload={"documents": 0, "conversations": 1},
        ),
        test_engine,
    )

    with Session(test_engine) as session:
        assert session.get(User, user_id) is None
        assert session.exec(select(Task)).all() == []
        assert session.exec(select(TaskDocument)).all() == []
        assert session.exec(select(Conversation)).all() == []
        assert session.exec(select(Message)).all() == []
        assert session.exec(select(LLMUsage)).all() == []

    assert client.get("/tasks", headers=alice_headers).status_code == 401


def test_token_version_revokes_old_token(
        client,
        test_engine,
        alice_headers,
):
    with Session(test_engine) as session:
        user = session.exec(select(User)).one()
        user.token_version += 1
        session.add(user)
        session.commit()

    assert client.get("/tasks", headers=alice_headers).status_code == 401
