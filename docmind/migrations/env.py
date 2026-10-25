"""DocMind 的 Alembic 环境:让迁移能连上 docmind 自己的库、能看到自己的模型。

结构与 taskflow 同构,但有一处刻意不同:Base 直接从 shared.db.base 导入,
而不是像 taskflow/migrations/env.py 那样写 `from app.db.base import Base`
——taskflow 的 app/ 下其实没有 db 目录,那一行是陈旧代码,照抄会直接跑挂。

【为什么 metadata 是共享的那一份?】
docmind 沿用 shared/db/base.py 的约定:Base.metadata 与 SQLModel.metadata
是同一份。autogenerate 时 alembic 只要拿到 Base.metadata,就能一次性看到
全部表(包括将来 SQLModel 写的表),不用区分模型用了哪种写法。
"""
import os
from logging.config import fileConfig

import sqlmodel
from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

# 先加载 docmind/.env(里面定义了 DATABASE_URL),再读配置
load_dotenv()


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


# 导入所有模型,把它们注册到共享 metadata 上,autogenerate 才能看到表结构。
# 每新增一张表,就在这里补一行 import(和 taskflow 的 env.py 同款)。
# conversation 模型也必须在这里 import，否则 autogenerate 看不到
# conversations / conversation_messages，新库迁移会漏建这两张表。
import app.models.conversation  # noqa: F401  注册 conversations / conversation_messages 表
import app.models.document  # noqa: F401  注册 documents 表
import app.models.document_chunk  # noqa: F401  注册 document_chunks 表
from shared.db.base import Base  # noqa: E402

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