"""引用原文接口的回归测试。

覆盖 GET /documents/{id}/chunks 与 .../chunks/{chunk_index}：
排序 / 分页 / 鉴权 / 跨用户隔离 / 片段不存在，以及工作台所需的 CORS 头。
"""
import pytest
from app.core.config import settings
from app.main import app
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from fastapi.testclient import TestClient
from shared.db.database import get_session
from shared.security import create_access_token
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

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
        tables=[Document.__table__, DocumentChunk.__table__],
    )
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def client(engine):
    def get_test_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
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


def _seed_document(
        engine,
        *,
        user_id: int = AUTH_USER_ID,
        filename: str = "handbook.pdf",
        chunk_count: int = 3,
) -> int:
    with Session(engine) as session:
        document = Document(
            filename=filename,
            file_path=f"/tmp/{filename}",
            user_id=user_id,
            status="completed",
        )
        session.add(document)
        session.commit()
        session.refresh(document)

        for index in range(chunk_count):
            session.add(DocumentChunk(
                document_id=document.id,
                user_id=user_id,
                content=f"第 {index} 段内容",
                chunk_index=index,
                page=index + 1,
            ))
        session.commit()
        return document.id


def test_requires_auth(client):
    response = client.get("/documents/1/chunks")

    assert response.status_code == 401


def test_list_chunks_is_ordered_and_paginated(client, engine):
    document_id = _seed_document(engine, chunk_count=3)

    response = client.get(
        f"/documents/{document_id}/chunks?skip=1&limit=1",
        headers=auth_headers(),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == 3
    assert [item["chunk_index"] for item in data["items"]] == [1]
    assert data["items"][0]["content"] == "第 1 段内容"
    assert data["items"][0]["page"] == 2


def test_get_single_chunk(client, engine):
    document_id = _seed_document(engine, chunk_count=3)

    response = client.get(
        f"/documents/{document_id}/chunks/2",
        headers=auth_headers(),
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "document_id": document_id,
        "chunk_index": 2,
        "page": 3,
        "content": "第 2 段内容",
    }


def test_missing_chunk_returns_404(client, engine):
    document_id = _seed_document(engine, chunk_count=1)

    response = client.get(
        f"/documents/{document_id}/chunks/99",
        headers=auth_headers(),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CHUNK_NOT_FOUND"


def test_other_users_document_returns_404(client, engine):
    other_users_document = _seed_document(engine, user_id=2)

    listing = client.get(
        f"/documents/{other_users_document}/chunks",
        headers=auth_headers(),
    )
    single = client.get(
        f"/documents/{other_users_document}/chunks/0",
        headers=auth_headers(),
    )

    # 不暴露"文档存在但不属于你"。
    assert listing.status_code == 404
    assert single.status_code == 404


def test_missing_document_returns_404(client):
    response = client.get(
        "/documents/999999/chunks",
        headers=auth_headers(),
    )

    assert response.status_code == 404


def test_cors_allows_workbench_origin(client):
    response = client.get(
        "/health",
        headers={"Origin": "http://localhost:3000"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == (
        "http://localhost:3000"
    )
