"""add document lifecycle fields and outbox_events

Revision ID: a8b4c1d7e2f9
Revises: e4b8d2f6a1c9
Create Date: 2026-11-11

- documents 支持软删除、索引版本、恢复请求标记；
- outbox_events 保证删除 / 重建索引 / 重命名 / 恢复事件和业务同事务。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a8b4c1d7e2f9"
down_revision: Union[str, Sequence[str], None] = "e4b8d2f6a1c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column(
            "index_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "restore_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        op.f("ix_documents_deleted_at"),
        "documents",
        ["deleted_at"],
        unique=False,
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_outbox_events_event_id"),
        "outbox_events",
        ["event_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_outbox_events_event_type"),
        "outbox_events",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_outbox_events_aggregate_id"),
        "outbox_events",
        ["aggregate_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_outbox_events_user_id"),
        "outbox_events",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_outbox_events_published_at"),
        "outbox_events",
        ["published_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_outbox_events_published_at"),
        table_name="outbox_events",
    )
    op.drop_index(
        op.f("ix_outbox_events_user_id"),
        table_name="outbox_events",
    )
    op.drop_index(
        op.f("ix_outbox_events_aggregate_id"),
        table_name="outbox_events",
    )
    op.drop_index(
        op.f("ix_outbox_events_event_type"),
        table_name="outbox_events",
    )
    op.drop_index(
        op.f("ix_outbox_events_event_id"),
        table_name="outbox_events",
    )
    op.drop_table("outbox_events")

    op.drop_index(
        op.f("ix_documents_deleted_at"),
        table_name="documents",
    )
    op.drop_column("documents", "restore_requested")
    op.drop_column("documents", "index_version")
    op.drop_column("documents", "deleted_at")
