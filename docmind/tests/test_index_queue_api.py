"""上传 / 重新索引接口与队列的集成测试。

队列用内存实现覆盖，不依赖真实 Redis；
验证：上传入队、队列不可用时的进程内兜底、重新索引的状态与归属校验。
"""
import pytest
from app.api import documents as documents_api
from app.core.config import settings
from app.main import app
from app.models.document import Document
from app.queue import get_index_queue
from fastapi.testclient import TestClient
from shared.db.database import get_session
from shared.security import create_access_token
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from .queue_fakes import MemoryIndexQueue

AUTH_USER_ID = 1


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


@pytest.fixture
def queue():
    return MemoryIndexQueue()


@pytest.fixture
def client(engine, queue, tmp_path, monkeypatch):
    # 上传文件写到临时目录，不污染 docmind/data。
    monkeypatch.setattr(documents_api, "UPLOAD_DIR", tmp_path)

    def get_test_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
    app.dependency_overrides[get_index_queue] = lambda: queue
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def auth_headers(user_id: int = AUTH_USER_ID) -> dict[str, str]:
    token = create_access_token(
        subject=f"user-{user_id}",
        user_id=user_id,
        secret_key=settings.JWT_SECRET_KEY,
    )
    return {"Authorization": f"Bearer {token}"}


def _upload(client, headers, content: bytes = b"hello world") -> dict:
    response = client.post(
        "/documents/upload",
        headers=headers,
        files={"file": ("policy.txt", content, "text/plain")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_upload_enqueues_index_job(client, queue):
    document = _upload(client, auth_headers())

    assert document["status"] == "pending"
    assert len(queue.ready) == 1
    job = queue.ready[0]
    assert job.document_id == document["id"]
    assert job.user_id == AUTH_USER_ID
    assert job.attempt == 0


def test_upload_falls_back_when_queue_unavailable(
        client,
        queue,
        monkeypatch,
):
    queue.unavailable = True
    fallback_calls: list[tuple] = []

    def fake_index_document(document_id, bind, *, raise_on_error=False):
        fallback_calls.append((document_id, raise_on_error))

    monkeypatch.setattr(
        documents_api,
        "index_document",
        fake_index_document,
    )

    document = _upload(client, auth_headers())

    # 队列挂了也要保住上传：退回进程内后台任务。
    assert document["status"] == "pending"
    assert fallback_calls == [(document["id"], False)]


def test_reindex_enqueues_and_resets_status(client, queue, engine):
    document = _upload(client, auth_headers())
    queue.ready.clear()

    response = client.post(
        f"/documents/{document['id']}/reindex",
        headers=auth_headers(),
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"
    assert len(queue.ready) == 1
    assert queue.ready[0].document_id == document["id"]


def test_reindex_clears_previous_error(client, engine):
    document = _upload(client, auth_headers())

    with Session(engine) as session:
        row = session.get(Document, document["id"])
        row.status = "failed"
        row.error_message = "embedding failed"
        session.add(row)
        session.commit()

    response = client.post(
        f"/documents/{document['id']}/reindex",
        headers=auth_headers(),
    )

    assert response.status_code == 200
    with Session(engine) as session:
        row = session.get(Document, document["id"])
        assert row.status == "pending"
        assert row.error_message is None


def test_reindex_rejects_processing_document(client, engine):
    document = _upload(client, auth_headers())

    with Session(engine) as session:
        row = session.get(Document, document["id"])
        row.status = "processing"
        session.add(row)
        session.commit()

    response = client.post(
        f"/documents/{document['id']}/reindex",
        headers=auth_headers(),
    )

    assert response.status_code == 409
    assert response.json()["error"]["message"] == (
        "document is being indexed"
    )


def test_reindex_other_users_document_returns_404(client):
    document = _upload(client, auth_headers(user_id=2))

    response = client.post(
        f"/documents/{document['id']}/reindex",
        headers=auth_headers(user_id=1),
    )

    assert response.status_code == 404
