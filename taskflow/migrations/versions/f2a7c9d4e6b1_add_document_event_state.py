"""add document event state and processed_events

Revision ID: f2a7c9d4e6b1
Revises: a4c9e2f71b30
Create Date: 2026-11-11

TaskFlow 消费 DocMind 的文档生命周期事件：
- task_documents 保存索引版本、失效标记和最后事件时间；
- task 保存来源索引版本、来源失效标记；
- processed_events 做事件幂等。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2a7c9d4e6b1"
down_revision: Union[str, Sequence[str], None] = "a4c9e2f71b30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "task",
        sa.Column("source_index_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "task",
        sa.Column("source_invalidated_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "task",
        sa.Column("source_invalid_reason", sa.String(), nullable=True),
    )
    op.add_column(
        "task",
        sa.Column("source_last_event_at", sa.DateTime(), nullable=True),
    )

    op.add_column(
        "task_documents",
        sa.Column(
            "index_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "task_documents",
        sa.Column("invalidated_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "task_documents",
        sa.Column("invalid_reason", sa.String(), nullable=True),
    )
    op.add_column(
        "task_documents",
        sa.Column("last_event_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )


def downgrade() -> None:
    op.drop_table("processed_events")

    op.drop_column("task_documents", "last_event_at")
    op.drop_column("task_documents", "invalid_reason")
    op.drop_column("task_documents", "invalidated_at")
    op.drop_column("task_documents", "index_version")

    op.drop_column("task", "source_last_event_at")
    op.drop_column("task", "source_invalid_reason")
    op.drop_column("task", "source_invalidated_at")
    op.drop_column("task", "source_index_version")
