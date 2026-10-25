"""启用 pgvector 扩展(DocMind 的迁移基线)

【这一版为什么只做这一件事?】
docmind 目前还没有任何业务表(RAG 的文档/chunk 表还没设计),所以初始迁移
先不建表,只把 vector 扩展装进 docmind 这个库——这是"数据库初始化时启用
vector 扩展"在 alembic 路径上的落点。

【为什么必须在迁移里再做一次,而不是只靠 docker initdb?】
- docker-entrypoint-initdb.d/init.sql 只在数据卷首次为空时跑一次;
  已有卷 / 换环境 / 未来的库都不会触发它。
- 迁移每次都对"当前这个库"执行,幂等(IF NOT EXISTS),所以能兜住所有场景。
- CREATE EXTENSION 是库级的:taskflow 那个库不用 vector 就不装,docmind 这个库装。
  镜像已经换成了 pgvector/pgvector:pg16(二进制预装好),
  这里只是把它登记进 docmind 库的 public schema。

【后续怎么演进】等 RAG 表模型写好(app/models/xxx.py,并在 env.py import),
跑 `alembic revision --autogenerate -m "..."`,新迁移会自动接在本次基线之后。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '4f6a2b9c1e8d'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 必须在任何引用 vector 类型 / 建 vector 索引的 DDL 之前执行。
    # IF NOT EXISTS 保证幂等:重复跑不会报错,也不会重复安装。
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    """Downgrade schema.

    刻意留空,不 DROP EXTENSION:
    删扩展成本高、风险大,几乎不存在"想撤销引入向量"的迁移场景,
    为可逆性去删一个库级扩展不值得——基线往前不回滚,业务表才需要 downgrade。
    """
    pass