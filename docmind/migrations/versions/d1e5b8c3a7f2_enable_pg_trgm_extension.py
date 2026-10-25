"""enable pg_trgm extension

Revision ID: d1e5b8c3a7f2
Revises: 9c2d7f4a1b6e
Create Date: 2026-10-23

【为什么需要这条迁移？】
混合检索的第三路用了 word_similarity()（app/repositories/vector_repository.py），
它来自 pg_trgm 扩展，不是 PostgreSQL 默认开启的功能。之前只有 vector 扩展的
迁移，导致 search_documents_hybrid 在干净数据库上直接报
"function word_similarity(...) does not exist"。

pg_trgm 是库级扩展，必须在每个要用的数据库里单独 CREATE EXTENSION。
放在迁移里而不是只靠 docker-entrypoint-initdb.d：后者只在数据卷首次为空时执行。
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd1e5b8c3a7f2'
down_revision: Union[str, Sequence[str], None] = '9c2d7f4a1b6e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def downgrade() -> None:
    """Downgrade schema.

    与 vector 扩展的基线保持一致：不 DROP EXTENSION。
    扩展是库级共享资源，删掉可能影响同库其它功能；基线不回滚。
    """
    pass
