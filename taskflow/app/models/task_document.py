"""任务 ↔ 文档的显式关联。

为什么只存整数 document_id、不建外键：
users / tasks 在 TaskFlow 库，documents 在 DocMind 库，跨库没有外键。
归属校验在应用层做（先校验任务属于当前用户，DocMind 侧再用 uid 过滤）。

filename 是链接时从 DocMind 取回的快照：任务详情 / 列表展示关联文档时
不必再请求一次 DocMind；文档改名或删除时，这里保留历史展示名。
"""
from datetime import datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class TaskDocument(SQLModel, table=True):
    __tablename__ = "task_documents"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "document_id",
            name="uq_task_documents_task_document",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="task.id", index=True)
    document_id: int = Field(index=True)
    filename: str | None = Field(default=None)
    # 关联时的 DocMind 索引版本；重建索引后与事件里的新版本比较。
    index_version: int = Field(default=0)
    invalidated_at: datetime | None = Field(default=None)
    invalid_reason: str | None = Field(default=None)
    # 最后处理事件的时间：旧事件不能覆盖新状态（删除 vs 恢复）。
    last_event_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
