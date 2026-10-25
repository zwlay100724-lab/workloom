"""纯检索接口的请求 / 响应模型。

和 ``/rag/query`` 的分工：
- ``/rag/query``：检索 + 生成答案，适合"问答"；
- ``/retrieval/search``：只检索，不调用 LLM。适合"任务详情相关知识"、
  工作台聚合搜索这类调用方自己处理片段的场景，省掉一次 LLM 调用。
"""
from typing import Literal

from pydantic import BaseModel, Field


class RetrievalSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=5000)
    # 取回多少条片段；上限防止客户端一次拉取过多内容。
    top_k: int = Field(default=5, ge=1, le=20)
    # 可选：限定在某个文档中搜索（归属由服务端校验）。
    document_id: int | None = Field(default=None, ge=1)
    # vector: 纯向量检索（更快）；hybrid: 向量 + 全文 + 关键词三路融合（更准）。
    mode: Literal["vector", "hybrid"] = "vector"


class RetrievedChunk(BaseModel):
    document_id: int
    filename: str | None = None
    chunk_index: int
    page: int | None = None
    content: str
    # vector 模式是相似度（越大越相关）；hybrid 模式是 RRF 融合分。
    score: float


class RetrievalSearchResponse(BaseModel):
    query: str
    mode: str
    results: list[RetrievedChunk]
