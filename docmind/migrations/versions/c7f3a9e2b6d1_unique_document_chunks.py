"""unique (document_id, chunk_index) on document_chunks

Revision ID: c7f3a9e2b6d1
Revises: d1e5b8c3a7f2
Create Date: 2026-10-24

【为什么需要这条迁移？】
索引流水线改成"删旧 chunk + 写新 chunk"后，重试本身不会重复；但唯一约束是
数据库层的最后防线：任何并发/异常路径都不应该让同一文档出现两个相同
chunk_index 的片段。

迁移前先清理历史重复（如果有）：保留 id 最小的一条。
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'c7f3a9e2b6d1'
down_revision: Union[str, Sequence[str], None] = 'd1e5b8c3a7f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 历史上重试/index 失败可能留下重复；先保留每组最小 id。
    op.execute(
        """
        DELETE FROM document_chunks a
        USING document_chunks b
        WHERE a.id > b.id
          AND a.document_id = b.document_id
          AND a.chunk_index = b.chunk_index
        """
    )
    op.create_index(
        'uq_document_chunks_document_chunk',
        'document_chunks',
        ['document_id', 'chunk_index'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        'uq_document_chunks_document_chunk',
        table_name='document_chunks',
    )
