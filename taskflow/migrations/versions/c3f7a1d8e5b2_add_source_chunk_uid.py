"""add stable source_chunk_uid to task

Revision ID: c3f7a1d8e5b2
Revises: b7d3e9f1a2c4
Create Date: 2026-11-18

任务来源引用保存片段内容的稳定身份（SHA-256）。
重建索引后，TaskFlow 用它向 DocMind 查询新的 chunk_index，
不再依赖旧序号。存量任务没有该字段，只能在下次重新生成时补齐。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c3f7a1d8e5b2"
down_revision: Union[str, Sequence[str], None] = "b7d3e9f1a2c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "task",
        sa.Column("source_chunk_uid", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_task_source_chunk_uid",
        "task",
        ["source_chunk_uid"],
    )


def downgrade() -> None:
    op.drop_index("ix_task_source_chunk_uid", table_name="task")
    op.drop_column("task", "source_chunk_uid")
