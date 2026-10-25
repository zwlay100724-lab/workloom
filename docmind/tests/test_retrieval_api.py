"""纯检索接口 /retrieval/search 的回归测试。

不连真实 Postgres / Embedding：
- documents 表建在 SQLite 上（DocumentChunk 的 pgvector 类型不需要）；
- RetrievalService 用 fake 覆盖，只验证接口契约：鉴权、归属、
  字段映射、vector / hybrid 两种模式的 score 来源。
"""
import pytest
from app.api.retrieval import get_retrieval_service
from app.core.config import settings
from app.main import app
from app.models.document import Document
from fastapi.testclient import TestClient
from langchain_core.documents import Document as LCDocument
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
    # 只建 documents 表：本测试不碰 chunk 表，避免 pgvector 类型。
    SQLModel.metadata.create_all(
        test_engine,
        tables=[Document.__table__],
    )
    yield test_engine
    test_engine.dispose()


class FakeRetrievalService:
    def __init__(self) -> None:
        self.documents: list[LCDocument] = []
        self.calls: list[dict] = []

    def retrieve(
            self,
            session,
            question: str,
            user_id: int,
            top_k: int | None = None,
            document_id: int | None = None,
    ) -> list[LCDocument]:
        self.calls.append({
            "mode": "vector",
            "question": question,
            "user_id": user_id,
            "top_k": top_k,
            "document_id": document_id,
        })
        return self.documents

    def retrieve_hybrid(
            self,
            session,
            question: str,
            user_id: int,
            top_k: int | None = None,
            document_id: int | None = None,
            candidate_k: int = 20,
    ) -> list[LCDocument]:
        self.calls.append({
            "mode": "hybrid",
            "question": question,
            "user_id": user_id,
            "top_k": top_k,
            "document_id": document_id,
        })
        return self.documents


@pytest.fixture
def fake_service() -> FakeRetrievalService:
    return FakeRetrievalService()


@pytest.fixture
def client(engine, fake_service):
    def get_test_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
    app.dependency_overrides[get_retrieval_service] = lambda: fake_service
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


def add_document(
        engine,
        *,
        user_id: int = AUTH_USER_ID,
        filename: str = "handbook.pdf",
) -> int:
    with Session(engine) as session:
        document = Document(
            filename=filename,
            file_path=f"/tmp/{filename}",
            user_id=user_id,
        )
        session.add(document)
        session.commit()
        session.refresh(document)
        return document.id


def chunk(
        document_id: int,
        *,
        chunk_index: int = 0,
        page: int | None = 1,
        content: str = "员工每年享有 10 天带薪年假。",
        **scores,
) -> LCDocument:
    return LCDocument(
        page_content=content,
        metadata={
            "document_id": document_id,
            "chunk_index": chunk_index,
            "page": page,
            "source": "handbook.pdf",
            **scores,
        },
    )


# ── 鉴权 ───────────────────────────────────────────────────────────────
def test_requires_auth(client):
    response = client.post(
        "/retrieval/search",
        json={"query": "年假"},
    )

    assert response.status_code == 401


# ── 字段映射 ───────────────────────────────────────────────────────────
def test_vector_search_maps_fields(
        client,
        engine,
        fake_service,
):
    document_id = add_document(engine, filename="handbook.pdf")
    fake_service.documents = [
        chunk(
            document_id,
            chunk_index=3,
            page=5,
            similarity=0.875,
        )
    ]

    response = client.post(
        "/retrieval/search",
        headers=auth_headers(),
        json={"query": "年假", "top_k": 3},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["query"] == "年假"
    assert data["mode"] == "vector"
    assert data["results"] == [
        {
            "document_id": document_id,
            "filename": "handbook.pdf",
            "chunk_index": 3,
            "page": 5,
            "content": "员工每年享有 10 天带薪年假。",
            "score": 0.875,
        }
    ]
    # 服务端解析出的 user_id 才是可信来源，不能来自请求体。
    assert fake_service.calls[0] == {
        "mode": "vector",
        "question": "年假",
        "user_id": AUTH_USER_ID,
        "top_k": 3,
        "document_id": None,
    }


def test_hybrid_search_uses_rrf_score(
        client,
        engine,
        fake_service,
):
    document_id = add_document(engine)
    fake_service.documents = [
        chunk(document_id, rrf_score=0.032)
    ]

    response = client.post(
        "/retrieval/search",
        headers=auth_headers(),
        json={"query": "年假", "mode": "hybrid"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "hybrid"
    assert response.json()["results"][0]["score"] == 0.032
    assert fake_service.calls[0]["mode"] == "hybrid"


def test_empty_results(client):
    response = client.post(
        "/retrieval/search",
        headers=auth_headers(),
        json={"query": "不存在的内容"},
    )

    assert response.status_code == 200
    assert response.json()["results"] == []


# ── 归属校验 ───────────────────────────────────────────────────────────
def test_document_id_must_belong_to_current_user(
        client,
        engine,
        fake_service,
):
    other_users_document = add_document(engine, user_id=2)

    response = client.post(
        "/retrieval/search",
        headers=auth_headers(),
        json={
            "query": "年假",
            "document_id": other_users_document,
        },
    )

    # 不存在和不属于当前用户统一 404，不泄露资源是否存在。
    assert response.status_code == 404
    assert fake_service.calls == []


# ── 参数校验 ───────────────────────────────────────────────────────────
def test_rejects_invalid_top_k(client):
    response = client.post(
        "/retrieval/search",
        headers=auth_headers(),
        json={"query": "年假", "top_k": 0},
    )

    assert response.status_code == 422
