"""任务详情「相关知识」的响应模型。

字段和 DocMind ``/retrieval/search`` 的返回一一对应，只是换个归属：
TaskFlow 不存文档内容，只在需要时按任务标题 / 描述去检索，
所以这里不引入任何文档表或跨库外键。
"""
from pydantic import BaseModel, Field


class KnowledgeChunk(BaseModel):
    document_id: int
    filename: str | None = None
    chunk_index: int
    page: int | None = None
    content: str
    score: float


class TaskKnowledgeResponse(BaseModel):
    task_id: int
    # 实际用于检索的 query（任务标题 + 描述），方便前端 / 调试排查。
    query: str
    results: list[KnowledgeChunk]


# ── 任务摘要（检索片段 → LLM 总结 + 引用）─────────────────────

class KnowledgeSummaryCitationSpec(BaseModel):
    """LLM 输出契约：一条结论及其依据片段。"""

    document_id: int
    chunk_index: int
    text: str = Field(min_length=1, max_length=500)


class TaskKnowledgeSummarySpec(BaseModel):
    """LLM 输出契约：任务摘要。"""

    summary: str = Field(min_length=1, max_length=2000)
    citations: list[KnowledgeSummaryCitationSpec] = Field(
        default_factory=list,
    )


class KnowledgeCitation(BaseModel):
    """对外返回的引用：在 LLM 引用基础上补文件名 / 页码。"""

    document_id: int
    chunk_index: int
    text: str
    filename: str | None = None
    page: int | None = None


class TaskKnowledgeSummaryResponse(BaseModel):
    task_id: int
    query: str
    summary: str
    citations: list[KnowledgeCitation]
    sources: list[KnowledgeChunk]
    mode: str
