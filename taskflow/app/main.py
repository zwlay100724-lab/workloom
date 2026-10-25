from dotenv import load_dotenv

# 必须在任何 app.* / shared.* import 之前加载 .env
# 因为 Settings 实例化、DB engine / redis 客户端的创建都在 import 时发生,
# 需要环境变量(或 .env)先就位。
load_dotenv()

from fastapi import FastAPI
from shared.exception_handlers import register_exception_handlers
from shared.logging import configure_logging
from shared.middleware import request_logging_middleware

from app.api import auth, health
from app.api.ai import router as ai_router
from app.api.conversations import router as conversations_router
from app.api.tasks import router as tasks_router

# 【数据库 schema 由 alembic 迁移负责,启动不再 create_all】
# 原 create_db_and_tables() 已移除。原因:
#   1. create_all 只能"建缺失的表",不能演进已有表(加列/改约束要靠迁移);
#   2. 它和 alembic 是两个并行来源——模型改了忘写迁移,dev 里会被 create_all
#      悄悄补上、一切正常,换环境 `alembic upgrade head` 却少一列,正是版本化
#      想消除的隐性漂移。
# 现在结构唯一来源是 taskflow/migrations/。本地起服务前先执行:
#   cd taskflow && ../.venv/bin/alembic upgrade head
# (测试另说:conftest.py 里用 create_all 在临时库建表,是测试的标准做法。)
app = FastAPI(title="Taskflow API")

# 统一注册异常 Handler。
register_exception_handlers(app)

# 普通后端接口。
app.include_router(auth.router)
app.include_router(tasks_router)  # 装载 API:应用启动时把这一整组路由注册进 FastAPI

# Conversation 接口。
app.include_router(conversations_router)

# AI 接口。
app.include_router(ai_router)

app.include_router(health.router)

app.middleware("http")(request_logging_middleware)

configure_logging()