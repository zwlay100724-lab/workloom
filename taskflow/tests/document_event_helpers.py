"""文档事件测试的共享 seed。"""
from datetime import datetime

from app.models.task import Task
from app.models.task_document import TaskDocument
from app.models.user import User
from shared.event_queue import Event
from sqlmodel import Session, select

DOC_ID = 10
USER_ID = 1


def make_event(
        event_type: str,
        payload: dict,
        *,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
) -> Event:
    return Event(
        event_id=event_id or f"evt-{event_type}-{datetime.utcnow().timestamp()}",
        event_type=event_type,
        aggregate_id=DOC_ID,
        user_id=USER_ID,
        payload=payload,
        occurred_at=(occurred_at or datetime.utcnow()).isoformat(),
    )


def seed(engine, *, index_version: int = 1):
    with Session(engine) as session:
        session.add(User(
            id=USER_ID,
            username="alice",
            hashed_password="hashed",
        ))
        task = Task(
            title="完成登录接口联调",
            owner_id=USER_ID,
            source_document_id=DOC_ID,
            source_filename="plan.pdf",
            source_chunk_index=0,
            source_index_version=index_version,
        )
        session.add(task)
        session.flush()
        session.add(TaskDocument(
            task_id=task.id,
            document_id=DOC_ID,
            filename="plan.pdf",
            index_version=index_version,
        ))
        session.commit()
        return task.id


def load(engine):
    with Session(engine) as session:
        task = session.exec(select(Task)).one()
        link = session.exec(select(TaskDocument)).one()
        return task, link
