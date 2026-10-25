
from pydantic import BaseModel, Field


class RAGQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=5000)
    # 可选：限定在某个文档中搜索
    document_id: int | None = Field(default=None, ge=1)
    # 可选：覆盖默认的 Top-K；上限防止客户端一次拉取过多片段。
    top_k: int | None = Field(default=None, ge=1, le=20)
    # 传了就带历史，不传就单轮
    conversation_id: int | None = Field(default=None, ge=1)

class SourceInfo(BaseModel):
    document: str | None
    page: int | None
    page_display: str | None
    chunk_index: int | None
    similarity: float
    snippet: str

class RAGQueryResponse(BaseModel):
    answer: str
    sources: list[SourceInfo]
