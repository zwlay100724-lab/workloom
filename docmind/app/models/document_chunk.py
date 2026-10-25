from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from app.core.config import settings


class DocumentChunk(SQLModel, table=True):
    """
    文档片段表：每个 Chunk 对应一条记录。
    向量列 embedding 在板块五加入（需要 pgvector 的 Vector 类型）。
    """
    __tablename__ = "document_chunks"

    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="documents.id", index=True)
    # 不建外键：users 表属 taskflow 的库（跨库无 FK，同 documents.user_id 的做法）
    user_id: int = Field(index=True)
    content: str  # Chunk 文本内容
    chunk_index: int  # 文档内的片段序号
    page: int | None = Field(default=None)  # 来自 PDF 的哪一页
    source: str | None = Field(default=None)  # 来源文件名
    # 向量列：维度从配置读取，确保和 Embedding 模型一致
    embedding: list[float] = Field(
        default=None,
        sa_column=Column(Vector(settings.EMBED_DIMENSION))
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)