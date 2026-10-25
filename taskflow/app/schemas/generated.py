"""「文档 → 任务」的结构化输出与请求模型。

GeneratedTaskSpec / GeneratedTaskList 同时也是 LLM 的 JSON 输出契约：
模型返回的内容一律当作不可信数据，先过 Pydantic 再落库。
"""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.task import TaskResponse


class GenerateTasksRequest(BaseModel):
    document_id: int = Field(ge=1)
    # 额外要求，例如"只关注测试相关的任务"。
    instruction: str | None = Field(default=None, max_length=1000)
    max_tasks: int = Field(default=5, ge=1, le=10)


class GeneratedTaskSpec(BaseModel):
    title: str = Field(min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=500)
    priority: Literal["low", "medium", "high"] = "medium"
    deadline: date | None = None
    # 任务依据所在的片段编号；服务端会校验它确实来自本次检索结果。
    source_chunk_index: int | None = None


class GeneratedTaskList(BaseModel):
    tasks: list[GeneratedTaskSpec] = Field(default_factory=list)


class GenerateTasksResponse(BaseModel):
    document_id: int
    filename: str | None = None
    tasks: list[TaskResponse]
