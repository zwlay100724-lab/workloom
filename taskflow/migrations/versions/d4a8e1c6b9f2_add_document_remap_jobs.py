"""add document_remap_jobs

Revision ID: d4a8e1c6b9f2
Revises: c3f7a1d8e5b2
Create Date: 2026-11-18

重建索引事件只登记迁移任务；独立 worker 代表文档所有者调用 DocMind，
按 chunk_uid 批量解析新的 chunk_index。同一文档 + 用户只保留一行。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d4a8e1c6b9f2"
down_revision: Union[str, Sequence[str], None] = "c3f7a1d8e5b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_remap_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "target_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "next_run_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "user_id",
            name="uq_document_remap_jobs_doc_user",
        ),
    )
    op.create_index(
        "ix_document_remap_jobs_document_id",
        "document_remap_jobs",
        ["document_id"],
    )
    op.create_index(
        "ix_document_remap_jobs_user_id",
        "document_remap_jobs",
        ["user_id"],
    )
    op.create_index(
        "ix_document_remap_jobs_status",
        "document_remap_jobs",
        ["status"],
    )
    op.create_index(
        "ix_document_remap_jobs_next_run_at",
        "document_remap_jobs",
        ["next_run_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_remap_jobs_next_run_at",
        table_name="document_remap_jobs",
    )
    op.drop_index(
        "ix_document_remap_jobs_status",
        table_name="document_remap_jobs",
    )
    op.drop_index(
        "ix_document_remap_jobs_user_id",
        table_name="document_remap_jobs",
    )
    op.drop_index(
        "ix_document_remap_jobs_document_id",
        table_name="document_remap_jobs",
    )
    op.drop_table("document_remap_jobs")
