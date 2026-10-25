"""任务 ↔ 文档关联接口的请求 / 响应模型。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TaskDocumentLinkRequest(BaseModel):
    document_id: int = Field(ge=1)


class TaskDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: int
    # 链接时从 DocMind 取回的文件名快照。
    filename: str | None = None
    created_at: datetime


class TaskDocumentListResponse(BaseModel):
    task_id: int
    items: list[TaskDocumentResponse]
