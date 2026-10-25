"""DocMind 应用入口(骨架)。

先只挂 shared 的统一异常处理器;
业务 router 等真正写接口时再在这里 include_router。
异常响应结构(taskflow 也是这套)直接复用 shared,
不需要 docmind 自己重新实现一遍 404/校验/LLM 错误处理。
"""
from contextlib import asynccontextmanager

from dotenv import load_dotenv

# 必须在任何 app.* / shared.* import 之前加载 .env
load_dotenv()

from fastapi import FastAPI, HTTPException
from shared.db.database import engine
from shared.exception_handlers import register_exception_handlers
from shared.logging import configure_logging
from shared.middleware import request_logging_middleware
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, text

from app.api.conversations import router as conversations_router
from app.api.documents import router as documents_router
from app.api.rag import router as rag_router
from app.api.retrieval import router as retrieval_router

# 后续板块会加更多 router

configure_logging()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 表结构统一由 alembic 管理；启动只兜底装 pgvector 扩展。
    # 测试环境用 SQLite，CREATE EXTENSION 不适用。
    if engine.dialect.name == "postgresql":
        with Session(engine) as session:
            session.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            session.commit()
    yield


app = FastAPI(
    title="DocMind 企业知识库",
    version="0.1.0",
    lifespan=lifespan,
)
app.middleware("http")(request_logging_middleware)

# 统一注册异常 Handler。
# 之前先 new 了一个 app 在这里注册、后面又 new 一个 app 覆盖——handler 全被丢掉，
# 必须挂在最终这一个 app 实例上。
register_exception_handlers(app)

# 注册路由
app.include_router(documents_router)
app.include_router(rag_router)  # 板块七加
app.include_router(retrieval_router)  # 纯检索：供 TaskFlow 等调用方使用
app.include_router(conversations_router)


@app.get("/health")
def health():
    """存活检查：进程能响应即可。"""
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready():
    """就绪检查：数据库可用才返回 200，供 compose / smoke 使用。"""
    try:
        with Session(engine) as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail="database unavailable",
        ) from exc
    return {"status": "ready"}