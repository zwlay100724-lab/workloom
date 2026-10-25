"""任务详情「相关知识」的响应模型。

字段和 DocMind ``/retrieval/search`` 的返回一一对应，只是换个归属：
TaskFlow 不存文档内容，只在需要时按任务标题 / 描述去检索，
所以这里不引入任何文档表或跨库外键。
"""
from pydantic import BaseModel


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
