"""「文档 → 任务」的结构化输出与请求模型。

生成流程分成两步：
1. ``/ai/tasks/from-document/preview``：检索 + LLM 提取，只返回建议，不写库；
2. ``/ai/tasks/from-document/apply``：用户确认（可编辑）后，把任务落库。

GeneratedTaskSpec / GeneratedTaskList 同时也是 LLM 的 JSON 输出契约：
模型返回的内容一律当作不可信数据，先过 Pydantic 再返回 / 落库。
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
    # 任务依据所在的片段编号；预览阶段由服务端校验，
    # apply 阶段作为用户确认后的引用原样保存。
    source_chunk_index: int | None = Field(default=None, ge=0)


class GeneratedTaskList(BaseModel):
    tasks: list[GeneratedTaskSpec] = Field(default_factory=list)


class PreviewTask(GeneratedTaskSpec):
    # 引用片段的摘要：让用户在确认前能看到任务依据的原文。
    source_snippet: str | None = None


class GenerateTasksPreviewResponse(BaseModel):
    document_id: int
    filename: str | None = None
    instruction: str | None = None
    tasks: list[PreviewTask]


class ApplyGeneratedTasksRequest(BaseModel):
    document_id: int = Field(ge=1)
    # 用户在预览基础上编辑后的任务；至少一条，上限与生成上限一致。
    tasks: list[GeneratedTaskSpec] = Field(min_length=1, max_length=10)


class GenerateTasksResponse(BaseModel):
    document_id: int
    filename: str | None = None
    tasks: list[TaskResponse]
