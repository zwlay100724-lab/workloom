"""SQLAlchemy engine / session。

【注意】这里只有"连接管理",没有 create_db_and_tables:
建表逻辑要 import 各 app 的模型(metadata 才知道有哪些表),
进 shared 会造成 shared → app 的倒挂依赖,所以它留在每个 app 自己的入口里。
"""
import os

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import Session, create_engine

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL is None:
    raise RuntimeError("DATABASE_URL is not configured")

# 默认关闭 SQL echo：生产/评测不需要把每条 SQL 打到 stdout。
# 本地排查时设置 DB_ECHO=true 即可。
DB_ECHO = os.getenv("DB_ECHO", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# Python 应用和数据库之间的连接管理器:FastAPI —— engine —— PostgreSQL
engine = create_engine(
    DATABASE_URL,
    echo=DB_ECHO,
)


def get_session():
    """同步 session 依赖。"""
    with Session(engine) as session:
        yield session


# --- 异步 engine / session(conversation 等功能用) ---
# 惰性创建:测试环境的 DATABASE_URL 是 sqlite+pysqlite,而 aiosqlite 未安装,
# 若在 import 时直接 create_async_engine 会炸掉测试,所以首次用到才创建。
_async_engine: AsyncEngine | None = None


def _get_async_engine() -> AsyncEngine:
    global _async_engine
    if _async_engine is None:
        _async_engine = create_async_engine(
            DATABASE_URL,
            echo=DB_ECHO,
        )
    return _async_engine


async def get_async_session():
    factory = async_sessionmaker(
        _get_async_engine(),
        expire_on_commit=False,
    )
    async with factory() as session:
        yield session