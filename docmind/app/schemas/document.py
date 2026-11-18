from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator


class DocumentRenameRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=200)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("filename cannot be empty")
        return value


class DocumentResponse(BaseModel):
    """文档信息响应"""
    id: int
    filename: str
    file_type: str
    status: str
    page_count: int | None
    chunk_count: int | None
    index_version: int = 0
    deleted_at: datetime | None = None
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
    chunk_uid: str | None = None
    page: int | None
    content: str

    model_config = {"from_attributes": True}


class ChunkUidResolutionResponse(BaseModel):
    """按 chunk_uid 在当前索引里查找片段。

    resolved=False 表示文档还在、但当前索引里已经没有这段内容；
    调用方应把它当成“原文已变化”，而不是文档删除。
    """
    document_id: int
    index_version: int
    resolved: bool
    chunk_uid: str
    chunk_index: int | None = None
    page: int | None = None


class ChunkUidBatchResolveRequest(BaseModel):
    """后台迁移按批查询：一次最多 200 个内容哈希。"""
    chunk_uids: list[
        Annotated[str, Field(min_length=1, max_length=64)]
    ] = Field(min_length=1, max_length=200)


class ChunkUidResolutionItem(BaseModel):
    chunk_uid: str
    resolved: bool
    chunk_index: int | None = None
    page: int | None = None


class ChunkUidBatchResolveResponse(BaseModel):
    document_id: int
    index_version: int
    results: list[ChunkUidResolutionItem]


class DocumentChunkListResponse(BaseModel):
    items: list[DocumentChunkResponse]
    total: int
