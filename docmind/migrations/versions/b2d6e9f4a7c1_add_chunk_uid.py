"""add stable chunk_uid to document_chunks

Revision ID: b2d6e9f4a7c1
Revises: c9e5f2a7b3d6
Create Date: 2026-11-18

chunk_uid 是片段内容的 SHA-256，跨索引版本保持不变。
重建索引后 TaskFlow 可以按它找回新的 chunk_index，不再依赖旧序号。

迁移时会为已有片段补算 uid；之后新写入的片段由
VectorRepository.add_chunks() 自动填充。
"""
import hashlib
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b2d6e9f4a7c1"
down_revision: Union[str, Sequence[str], None] = "c9e5f2a7b3d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column("chunk_uid", sa.String(length=64), nullable=True),
    )

    # 存量片段按内容补算 uid。文档片段量级不大，这里一次性扫描即可；
    # 如果后续数据量很大，可以改成按 id 分批。
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, content FROM document_chunks "
            "WHERE chunk_uid IS NULL"
        )
    ).fetchall()
    for row in rows:
        uid = hashlib.sha256(
            (row.content or "").strip().encode("utf-8")
        ).hexdigest()
        bind.execute(
            sa.text(
                "UPDATE document_chunks SET chunk_uid = :uid WHERE id = :id"
            ),
            {"uid": uid, "id": row.id},
        )

    op.create_index(
        "ix_document_chunks_chunk_uid",
        "document_chunks",
        ["chunk_uid"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_chunks_chunk_uid",
        table_name="document_chunks",
    )
    op.drop_column("document_chunks", "chunk_uid")
