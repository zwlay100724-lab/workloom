"""DocMind 测试环境。

必须在 import app.* / shared.* 之前设置环境变量：
- shared.db.database 在 import 时读取 DATABASE_URL 并建 engine；
- app.core.config 的 Settings 在 import 时实例化。

用 setdefault 而不是直接赋值：本地已导出的环境变量优先，
但默认值保证测试不会去连真实的 Postgres / 调用真实 LLM。
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("LLM_API_KEY", "test-only-llm-key")
os.environ.setdefault("EMBED_API_KEY", "test-only-embed-key")
os.environ.setdefault(
    "JWT_SECRET_KEY",
    "test-only-jwt-secret-0123456789abcdef",
)

import pytest
from app.models.conversation import Conversation, ConversationMessage
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.outbox_event import OutboxEvent
from app.models.processed_event import ProcessedEvent
from app.services import document_service as document_service_module
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel


@pytest.fixture
def lifecycle_engine(monkeypatch):
    """文档生命周期测试用的内存库：只建需要的表，且不连真实 Redis。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            Document.__table__,
            DocumentChunk.__table__,
            OutboxEvent.__table__,
        ],
    )
    monkeypatch.setattr(
        document_service_module,
        "safe_publish_pending_events",
        lambda bind: None,
    )
    yield engine
    engine.dispose()


@pytest.fixture
def account_engine(monkeypatch):
    """用户注销清理测试：文档 / 片段 / 会话 / outbox / 幂等表。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            Document.__table__,
            DocumentChunk.__table__,
            Conversation.__table__,
            ConversationMessage.__table__,
            OutboxEvent.__table__,
            ProcessedEvent.__table__,
        ],
    )
    from app.services import account_cleanup_service

    monkeypatch.setattr(
        account_cleanup_service,
        "safe_publish_pending_events",
        lambda bind: None,
    )
    yield engine
    engine.dispose()
