"""重建索引后的来源引用迁移任务。

事件 handler 只负责在本地事务里登记任务；真正调用 DocMind
按 chunk_uid 解析新序号由独立 worker 完成，避免 HTTP 阻塞事件消费。

一个 (document_id, user_id) 只保留一行：同一文档连续重建时，
把 target_version 抬高并重新排队即可，不会堆积大量重复任务。
"""
from datetime import datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class RemapJob(SQLModel, table=True):
    __tablename__ = "document_remap_jobs"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "user_id",
            name="uq_document_remap_jobs_doc_user",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(index=True)
    user_id: int = Field(index=True)
    # 领到任务时的目标索引版本；worker 期间如果有更新的重建，会把它调大。
    target_version: int = Field(default=0)
    # pending / running / done / dead
    status: str = Field(default="pending", index=True, max_length=16)
    attempts: int = Field(default=0)
    next_run_at: datetime = Field(
        default_factory=datetime.utcnow,
        index=True,
    )
    last_error: str | None = Field(default=None, max_length=1000)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
