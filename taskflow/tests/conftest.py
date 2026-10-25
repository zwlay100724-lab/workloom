import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import (
    Session,
    SQLModel,
    create_engine,
)

# 必须放在导入 app 之前
# 注意:app.main 里的 load_dotenv() 在 conftest 之后才执行,
# 所以 shared.db.database 等模块 import 时要的环境变量必须在这里先设好。
os.environ.setdefault("JWT_SECRET_KEY","test-only-secret-key-for-pytest-2026",)
os.environ.setdefault("DATABASE_URL","sqlite+pysqlite:///:memory:",)
os.environ.setdefault("REDIS_URL","redis://localhost:6379/0",)
# Settings() 在 import app 时就会被构造(经 auth_service → config),
# BaseConfig 必填 llm_api_key,所以这里也要给,别依赖 taskflow/.env 存在。
os.environ.setdefault("LLM_API_KEY","test-only-llm-api-key",)

from app.main import app

# 确保模型已经注册进 SQLModel.metadata
from shared.db.database import get_session


@pytest.fixture
def test_engine():
    engine = create_engine(
        "sqlite://",#表示使用内存数据库，数据不会长期保存
        connect_args={"check_same_thread": False,},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all( engine)
    engine.dispose()


@pytest.fixture
def client(test_engine):
    def get_test_session():
        with Session(test_engine) as session:
            yield session
    app.dependency_overrides[get_session] = get_test_session
    test_client = TestClient(
        app,
        raise_server_exceptions=False,
    )
    yield test_client
    test_client.close()
    app.dependency_overrides.clear()

@pytest.fixture
def alice_token(client,):
    client.post(
        "/register",
        json={
            "username": "alice",
            "password": "FastAPI123",
        },
    )
    response = client.post(
        "/login",
        json={
            "username": "alice",
            "password": "FastAPI123",
        },
    )
    return response.json()[
        "access_token"
    ]

@pytest.fixture
def alice_headers(alice_token,):
    return {
        "Authorization":
        f"Bearer {alice_token}"
    }

