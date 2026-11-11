"""add account deletion, token revocation and usage state

Revision ID: b7d3e9f1a2c4
Revises: f2a7c9d4e6b1
Create Date: 2026-11-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7d3e9f1a2c4"
down_revision: Union[str, Sequence[str], None] = "f2a7c9d4e6b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "users",
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "token_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "monthly_token_quota",
            sa.Integer(),
            nullable=False,
            server_default="1000000",
        ),
    )

    op.add_column(
        "llm_usage",
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default="taskflow",
        ),
    )
    op.add_column(
        "llm_usage",
        sa.Column(
            "operation",
            sa.String(length=30),
            nullable=False,
            server_default="chat",
        ),
    )
    op.create_index(
        "ix_llm_usage_user_created",
        "llm_usage",
        ["user_id", "created_at"],
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

    op.drop_index("ix_llm_usage_user_created", table_name="llm_usage")
    op.drop_column("llm_usage", "operation")
    op.drop_column("llm_usage", "source")

    op.drop_column("users", "monthly_token_quota")
    op.drop_column("users", "token_version")
    op.drop_column("users", "deleted_at")
    op.drop_column("users", "status")
