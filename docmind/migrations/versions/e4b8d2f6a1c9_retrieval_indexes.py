"""add vector / full-text / trigram indexes

Revision ID: e4b8d2f6a1c9
Revises: c7f3a9e2b6d1
Create Date: 2026-10-24

索引说明：
- HNSW + vector_cosine_ops：匹配 `embedding <=> query_vec`；
- GIN(to_tsvector('simple', content))：匹配 hybrid_search 的全文一路；
- GIN(content gin_trgm_ops)：为 trigram 相关查询做准备；
- (user_id, document_id) 复合索引：覆盖按用户/文档过滤的检索路径。

pgvector 0.5+ 支持 HNSW；当前环境是 0.5.0。
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'e4b8d2f6a1c9'
down_revision: Union[str, Sequence[str], None] = 'c7f3a9e2b6d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_chunks_embedding_hnsw
        ON document_chunks
        USING hnsw (embedding vector_cosine_ops)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_chunks_content_fts
        ON document_chunks
        USING gin (to_tsvector('simple', content))
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_chunks_content_trgm
        ON document_chunks
        USING gin (content gin_trgm_ops)
        """
    )
    op.create_index(
        'ix_document_chunks_user_document',
        'document_chunks',
        ['user_id', 'document_id'],
    )


def downgrade() -> None:
    op.drop_index(
        'ix_document_chunks_user_document',
        table_name='document_chunks',
    )
    op.execute('DROP INDEX IF EXISTS ix_document_chunks_content_trgm')
    op.execute('DROP INDEX IF EXISTS ix_document_chunks_content_fts')
    op.execute('DROP INDEX IF EXISTS ix_document_chunks_embedding_hnsw')
