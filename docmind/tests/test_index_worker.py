"""索引 worker 的单元测试：成功、退避重试、最终放弃、崩溃遗留恢复。"""
from datetime import datetime, timedelta

import pytest
from app.models.document import Document
from app.queue.base import IndexJob
from app.queue.worker import IndexWorker
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from .queue_fakes import MemoryIndexQueue


class FakeIndexer:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls: list[int] = []

    def __call__(self, document_id: int) -> None:
        self.calls.append(document_id)
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("embedding failed")


def _worker(queue, indexer, *, max_attempts=3, base_delay=5.0, bind=None):
    return IndexWorker(
        queue,
        bind=bind,
        indexer=indexer,
        max_attempts=max_attempts,
        retry_base_delay=base_delay,
        stale_minutes=10,
    )


def test_job_payload_roundtrip_and_next_attempt():
    job = IndexJob(document_id=7, user_id=3, attempt=1)

    decoded = IndexJob.from_payload(job.to_payload())

    assert decoded == job
    assert job.next_attempt() == IndexJob(
        document_id=7,
        user_id=3,
        attempt=2,
    )


def test_success_does_not_requeue():
    queue = MemoryIndexQueue()
    indexer = FakeIndexer()
    queue.enqueue(IndexJob(document_id=7, user_id=3))
    worker = _worker(queue, indexer)

    processed = worker.run_once(timeout_seconds=0)

    assert processed is True
    assert indexer.calls == [7]
    assert queue.ready == []
    assert queue.delayed == []


def test_failure_requeues_with_exponential_backoff():
    queue = MemoryIndexQueue()
    indexer = FakeIndexer(failures=1)
    queue.enqueue(IndexJob(document_id=7, user_id=3, attempt=0))
    worker = _worker(queue, indexer, base_delay=5.0)

    worker.run_once(timeout_seconds=0)

    assert indexer.calls == [7]
    assert len(queue.delayed) == 1
    delay, job = queue.delayed[0]
    assert delay == 5.0
    assert job.attempt == 1


def test_gives_up_after_max_attempts():
    queue = MemoryIndexQueue()
    indexer = FakeIndexer(failures=1)
    # attempt=1 表示这是最后一次尝试（max_attempts=2）。
    queue.enqueue(IndexJob(document_id=7, user_id=3, attempt=1))
    worker = _worker(queue, indexer, max_attempts=2)

    worker.run_once(timeout_seconds=0)

    assert indexer.calls == [7]
    assert queue.ready == []
    assert queue.delayed == []


def test_delayed_job_is_promoted_before_dequeue():
    queue = MemoryIndexQueue()
    indexer = FakeIndexer()
    queue.enqueue(
        IndexJob(document_id=7, user_id=3),
        delay_seconds=30,
    )
    worker = _worker(queue, indexer)

    processed = worker.run_once(timeout_seconds=0)

    assert processed is True
    assert indexer.calls == [7]


def test_run_once_returns_false_when_queue_is_empty():
    worker = _worker(MemoryIndexQueue(), FakeIndexer())

    assert worker.run_once(timeout_seconds=0) is False


@pytest.fixture
def engine():
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        test_engine,
        tables=[Document.__table__],
    )
    yield test_engine
    test_engine.dispose()


def test_retry_marks_document_pending_again(engine):
    with Session(engine) as session:
        document = Document(
            filename="a.pdf",
            file_path="/tmp/a.pdf",
            user_id=1,
            status="failed",
            error_message="embedding failed",
        )
        session.add(document)
        session.commit()
        session.refresh(document)
        document_id = document.id

    queue = MemoryIndexQueue()
    queue.enqueue(IndexJob(document_id=document_id, user_id=1))
    worker = _worker(
        queue,
        FakeIndexer(failures=1),
        bind=engine,
    )

    worker.run_once(timeout_seconds=0)

    with Session(engine) as session:
        assert session.get(Document, document_id).status == "pending"
    assert len(queue.delayed) == 1


def test_recover_stale_processing_documents(engine):
    old = datetime.utcnow() - timedelta(minutes=60)
    with Session(engine) as session:
        session.add(Document(
            filename="stale.pdf",
            file_path="/tmp/stale.pdf",
            user_id=1,
            status="processing",
            updated_at=old,
        ))
        session.add(Document(
            filename="fresh.pdf",
            file_path="/tmp/fresh.pdf",
            user_id=1,
            status="processing",
            updated_at=datetime.utcnow(),
        ))
        session.add(Document(
            filename="done.pdf",
            file_path="/tmp/done.pdf",
            user_id=1,
            status="completed",
            updated_at=old,
        ))
        session.commit()

    queue = MemoryIndexQueue()
    worker = _worker(queue, FakeIndexer(), bind=engine)

    recovered = worker.recover_stale()

    assert recovered == 1
    assert len(queue.ready) == 1
    assert queue.ready[0].user_id == 1
