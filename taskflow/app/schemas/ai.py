
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=5000)


class ChatResponse(BaseModel):
    answer: str

class StructuredTaskRequest(BaseModel):
    """结构化输出 / 意图解析三个接口共用的请求体。

    字段名统一为 message，与 ChatRequest / ToolChatRequest 保持一致。
    不要在文件后面再定义同名类：后定义的类会覆盖前者，导致接口访问
    payload.message 时抛 AttributeError（曾因此让三个接口全部 500）。
    """
    message: str = Field(
        min_length=1,
        max_length=4000,
    )


class StructuredTaskData(BaseModel):
    title: str = Field(
        min_length=1,
        max_length=200,
    )

    priority: Literal[
        "low",
        "medium",
        "high",
    ]

    deadline: date | None = None


class StructuredTaskResponse(BaseModel):
    data: StructuredTaskData


class CreateTaskToolArguments(BaseModel):
    title: str = Field(
        min_length=1,
        max_length=200,
    )

    description: str | None = None

    priority: Literal[
        "low",
        "medium",
        "high",
    ] = "medium"

    deadline: date | None = None

class ToolChatRequest(BaseModel):
    message: str = Field(
        min_length=1,
        max_length=4000,
    )


class ToolChatResponse(BaseModel):
    answer: str


class PlanRequest(BaseModel):
    # 用户输入的复杂目标
    message: str = Field(
        min_length=1,
        max_length=4000,
    )


class PlannedTask(BaseModel):
    # 每一个拆分后的子任务
    title: str = Field(
        min_length=1,
        max_length=200,
    )

    priority: Literal[
        "low",
        "medium",
        "high",
    ]

    reason: str = Field(
        min_length=1,
        max_length=500,
    )


class TaskPlan(BaseModel):
    # 模型首先总结用户真正的目标
    goal: str
    # 拆分后的任务
    tasks: list[PlannedTask]
    # 如果模型发现缺少关键信息，可在这里提醒用户
    notes: str | None = None

class TaskIntent(BaseModel):
    """
    LLM 从自然语言中识别出来的“任务意图”。

    注意：
    这是 AI 层的数据结构，
    不等于数据库 Task Model。
    """

    title: str = Field(
        description="用户希望创建或处理的任务标题"
    )

    description: str | None = Field(
        default=None,
        description="任务的补充说明；用户没有提供时返回 null",
    )

    priority: Literal["low", "medium", "high"] = Field(
        default="medium",
        description="任务优先级，只允许 low、medium、high",
    )

    due_text: str | None = Field(
        default=None,
        description="用户原话中的时间表达，例如：明天下午、周五之前",
    )
