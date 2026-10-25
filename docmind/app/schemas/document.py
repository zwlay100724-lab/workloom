from datetime import datetime

from pydantic import BaseModel


class DocumentResponse(BaseModel):
    """文档信息响应"""
    id: int
    filename: str
    file_type: str
    status: str
    page_count: int | None
    chunk_count: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentListResponse(BaseModel):
    """文档列表响应"""
    items: list[DocumentResponse]
    total: int