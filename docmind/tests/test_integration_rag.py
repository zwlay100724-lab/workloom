"""DocMind 集成测试：真实 PostgreSQL + pgvector + pg_trgm。

覆盖：
  上传 → 解析 / 切分 / 向量化 → 向量检索 / 混合检索
  → LangGraph ReAct 问答 → 会话消息 → 跨用户隔离 → 删除

外部依赖：
  - 真实 PostgreSQL（从 docmind/.env 的 DATABASE_URL 派生 docmind_test 库）；
  - LLM / Embedding 用确定性 fake 替换，不消耗 API 额度；
  - 加载器、切分器、pgvector、SQL、LangGraph 节点都是真实执行。

如果没有可用的 PostgreSQL，整组测试会 skip。
"""
import os
import time
from pathlib import Path

import pytest
from app.core.config import settings
from app.main import app
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.services.document_service import DocumentService
from app.services.retrieval_service import RetrievalService
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from shared.db.database import get_session
from shared.security import create_access_token
from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, func, select

pytestmark = pytest.mark.integration


# ── 真实 PostgreSQL 测试库 ──────────────────────────────────────────────
@pytest.fixture(scope="session")
def test_engine():
    # 优先级：
    # 1) CI 显式提供的测试库（不要求 docmind/.env 存在）；
    # 2) 本地 docmind/.env（真实开发配置）；
    # 3) 进程环境变量（可能被 conftest 设成 SQLite，此时会 skip）。
    env_path = Path(__file__).resolve().parents[1] / ".env"
    base_url = os.getenv("DOCMIND_TEST_DATABASE_URL")
    if not base_url and env_path.exists():
        base_url = dotenv_values(env_path).get("DATABASE_URL")
    base_url = base_url or os.getenv("DATABASE_URL")

    if not base_url or not base_url.startswith("postgresql"):
        pytest.skip(
            "集成测试需要 PostgreSQL：设置 DOCMIND_TEST_DATABASE_URL "
            "或配置 docmind/.env"
        )

    base = make_url(base_url)
    test_url = base.set(database="docmind_test")

    admin = create_engine(
        base.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname='docmind_test'")
            ).scalar_one_or_none()
            if not exists:
                conn.execute(text("CREATE DATABASE docmind_test"))
    except Exception as exc:
        admin.dispose()
        pytest.skip(f"PostgreSQL 不可用: {exc}")
    admin.dispose()

    engine = create_engine(test_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

        # 测试库每次重建表结构，避免受上一次运行影响。
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
    except Exception as exc:
        engine.dispose()
        pytest.skip(f"初始化 docmind_test 失败: {exc}")

    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_db(test_engine):
    """每个测试开始前清空业务表。"""
    with test_engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE document_chunks, documents, "
            "conversation_messages, conversations "
            "RESTART IDENTITY CASCADE"
        ))
    yield


# ── 测试替身 ───────────────────────────────────────────────────────────
class FakeEmbeddings:
    """固定向量：让 pgvector 检索可预测，避免调用 DashScope。"""

    def __init__(self) -> None:
        self.dimension = settings.EMBED_DIMENSION

    def _vector(self) -> list[float]:
        return [0.01] * self.dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector() for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector()


class FakeToolCallingModel:
    """第一次要求调用 search_documents，第二次给出最终回答。"""

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, messages, config=None):
        self.calls += 1
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "search_documents",
                        "args": {"query": "年假", "top_k": 3},
                    }
                ],
            )
        return AIMessage(content="正式员工每年有 10 天带薪年假。")


@pytest.fixture
def fake_embeddings(monkeypatch):
    fake = FakeEmbeddings()
    # 上传/索引和检索各自 import 了 get_embeddings，需要分别替换模块属性。
    monkeypatch.setattr(
        "app.services.document_service.get_embeddings",
        lambda: fake,
    )
    monkeypatch.setattr(
        "app.services.retrieval_service.get_embeddings",
        lambda: fake,
    )
    return fake


@pytest.fixture
def fake_llm(monkeypatch):
    model = FakeToolCallingModel()
    # nodes.py 在模块级绑定 _llm_with_tools，替换它即可让图走假模型。
    monkeypatch.setattr("app.graph.nodes._llm_with_tools", model)
    return model


# ── API / 鉴权辅助 ─────────────────────────────────────────────────────
@pytest.fixture
def client(test_engine, tmp_path, monkeypatch):
    # 上传文件写到 pytest 的临时目录，不污染 docmind/data。
    monkeypatch.setattr("app.api.documents.UPLOAD_DIR", tmp_path)

    def get_test_session():
        with Session(test_engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def auth_headers(user_id: int) -> dict[str, str]:
    token = create_access_token(
        subject=f"user-{user_id}",
        user_id=user_id,
        secret_key=settings.JWT_SECRET_KEY,
    )
    return {"Authorization": f"Bearer {token}"}


def wait_for_document(
    client: TestClient,
    headers: dict[str, str],
    doc_id: int,
    timeout: float = 60,
) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        response = client.get(
            f"/documents/{doc_id}",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] in {"completed", "failed"}:
            return last
        time.sleep(0.1)
    raise AssertionError(f"文档 {doc_id} 索引超时: {last}")


def upload_text(
    client: TestClient,
    headers: dict[str, str],
    content: str = "员工年假为 10 天。病假每年最多 15 天。",
) -> dict:
    response = client.post(
        "/documents/upload",
        files={
            "file": (
                "policy.txt",
                content.encode("utf-8"),
                "text/plain",
            )
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text

    # 上传接口现在是后台索引，先返回 pending；轮询到终态再断言。
    doc = wait_for_document(client, headers, response.json()["id"])
    assert doc["status"] == "completed", doc
    return doc


# ── 测试 ───────────────────────────────────────────────────────────────
def test_upload_index_and_retrieval(client, fake_embeddings, test_engine):
    headers = auth_headers(1)

    doc = upload_text(client, headers)

    assert doc["chunk_count"] >= 1

    with Session(test_engine) as session:
        service = RetrievalService()

        vector_docs = service.retrieve(
            session,
            "年假",
            user_id=1,
            top_k=5,
        )
        assert vector_docs
        assert "年假" in "".join(
            item.page_content for item in vector_docs
        )

        hybrid_docs = service.retrieve_hybrid(
            session,
            "年假",
            user_id=1,
            top_k=5,
        )
        assert hybrid_docs

        # 不同用户不能检索到这份文档
        assert service.retrieve(session, "年假", user_id=2, top_k=5) == []


def test_rag_query_with_conversation(
    client,
    fake_embeddings,
    fake_llm,
):
    headers = auth_headers(1)

    conversation = client.post(
        "/conversations",
        json={"title": "年假咨询"},
        headers=headers,
    )
    assert conversation.status_code == 201, conversation.text
    conversation_id = conversation.json()["id"]

    doc = upload_text(client, headers)

    response = client.post(
        "/rag/query",
        json={
            "question": "年假有多少天？",
            "document_id": doc["id"],
            "conversation_id": conversation_id,
            "top_k": 3,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["answer"] == "正式员工每年有 10 天带薪年假。"
    assert payload["sources"]

    messages = client.get(
        f"/conversations/{conversation_id}/messages",
        headers=headers,
    )
    assert messages.status_code == 200, messages.text

    body = messages.json()
    assert body["total"] == 2
    assert [item["role"] for item in body["items"]] == [
        "user",
        "assistant",
    ]
    assert body["items"][0]["content"] == "年假有多少天？"


def test_cross_user_isolation(client, fake_embeddings):
    headers = auth_headers(1)
    other_headers = auth_headers(2)

    conversation = client.post(
        "/conversations",
        json={"title": "private"},
        headers=headers,
    ).json()
    conversation_id = conversation["id"]
    doc = upload_text(client, headers)

    # 列表只返回自己的数据
    assert client.get("/documents", headers=other_headers).json()["total"] == 0
    assert (
        client.get("/conversations", headers=other_headers).json()["total"]
        == 0
    )

    # 不能读别人的会话
    assert (
        client.get(
            f"/conversations/{conversation_id}/messages",
            headers=other_headers,
        ).status_code
        == 404
    )

    # 不能删别人的文档
    assert (
        client.delete(
            f"/documents/{doc['id']}",
            headers=other_headers,
        ).status_code
        == 404
    )

    # 不能借别人的 document_id / conversation_id 发起问答
    assert (
        client.post(
            "/rag/query",
            json={"question": "年假", "document_id": doc["id"]},
            headers=other_headers,
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/rag/query",
            json={"question": "年假", "conversation_id": conversation_id},
            headers=other_headers,
        ).status_code
        == 404
    )


def test_reindex_is_idempotent(client, fake_embeddings, test_engine):
    headers = auth_headers(1)
    doc = upload_text(client, headers)

    with Session(test_engine) as session:
        before = session.exec(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.document_id == doc["id"])
        ).one()

    # 直接重跑索引流水线，模拟失败重试 / 手动重建。
    with Session(test_engine) as session:
        DocumentService(session).process_pdf(doc["id"])

    with Session(test_engine) as session:
        after = session.exec(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.document_id == doc["id"])
        ).one()
        document = session.get(Document, doc["id"])

    assert before >= 1
    assert after == before
    assert document is not None
    assert document.status == "completed"
    assert document.chunk_count == after


def test_delete_document_removes_chunks(
    client,
    fake_embeddings,
    test_engine,
):
    headers = auth_headers(1)
    doc = upload_text(client, headers)

    with Session(test_engine) as session:
        assert RetrievalService().retrieve(
            session,
            "年假",
            user_id=1,
            top_k=5,
        )

    response = client.delete(
        f"/documents/{doc['id']}",
        headers=headers,
    )
    assert response.status_code == 200, response.text

    with Session(test_engine) as session:
        assert RetrievalService().retrieve(
            session,
            "年假",
            user_id=1,
            top_k=5,
        ) == []

    assert client.get("/documents", headers=headers).json()["total"] == 0
