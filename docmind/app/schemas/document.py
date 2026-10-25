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


class DocumentChunkResponse(BaseModel):
    """单个片段的原文，供引用追溯 / 文档阅读器使用。"""
    document_id: int
    chunk_index: int
    page: int | None
    content: str

    model_config = {"from_attributes": True}


class DocumentChunkListResponse(BaseModel):
    items: list[DocumentChunkResponse]
    total: int