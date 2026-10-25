import os
from logging.config import fileConfig

import sqlmodel
from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool


def render_item(type_, obj, autogen_context):
    """把 SQLModel 的 AutoString 渲染成可移植的 sa.String。

    SQLModel 的 str 字段在 autogenerate 时会产出
    ``sqlmodel.sql.sqltypes.AutoString``,而迁移文件里只 import 了 sqlalchemy,
    直接跑会在 "sqlmodel is not defined" 上报错。这里统一转成 sa.String。
    """
    if (
        type_ == "type"
        and isinstance(obj, sqlmodel.sql.sqltypes.AutoString)
    ):
        autogen_context.imports.add("import sqlalchemy as sa")
        return f"sa.String(length={obj.length!r})"
    return False

# 先加载 taskflow/.env(里面定义了 DATABASE_URL),再读配置
load_dotenv()

# 导入所有模型,把它们注册到共享的 metadata 上,autogenerate 才能看到表结构
# (Base.metadata 与 SQLModel.metadata 是同一份,见 shared/db/base.py)
# 【为什么从 shared 导入?】共享包抽取时 taskflow 原来的 app/db/ 被删了,
# 这里不能再 `from app.db.base import Base`(会 ImportError)。
# taskflow 两种模型——纯 SQLAlchemy 的 conversation 等用 shared.db.base.Base,
# SQLModel 的 User/Task 共享同一份 metadata——都注册在这份 Base 上。
import app.models.conversation  # noqa: F401
import app.models.llm_usage  # noqa: F401
import app.models.task  # noqa: F401
import app.models.task_document  # noqa: F401
import app.models.user  # noqa: F401
from shared.db.base import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# 用 .env 里的真实连接串覆盖 alembic.ini 里的占位 URL
config.set_main_option(
    "sqlalchemy.url",
    os.environ["DATABASE_URL"],
)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_item=render_item,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_item=render_item,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
