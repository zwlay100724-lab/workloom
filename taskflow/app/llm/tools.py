"""
LangChain Tools 定义层
职责：将 TaskService 的业务能力包装成 LLM 可调用的 Tool
原则：Tool 只做参数转发，不写业务逻辑，不直接操作数据库
"""
from datetime import date
from typing import Literal

from app.clients.docmind import DocMindClient, DocMindUnavailableError
from app.core.exceptions import (
    TaskForbiddenError,
    TaskNotFoundError,
)
from app.models.task import Task
from app.schemas.task import TaskCreate, TaskPatch
from app.services import task_service
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session

# ============================================================
# 1. 每个 Tool 的输入 Schema
#    用 Field 的 description 告诉 LLM 每个参数的含义和约束。
#    字段名直接对齐 TaskService 的参数名,做到"只转发、不改名"。
#    (示例里的 status / due_date 在本项目里对应 done(bool) /
#     deadline(date),所以这里按真实模型写。)
# ============================================================

class GetTasksInput(BaseModel):
    """查询任务列表的参数"""
    done: bool | None = Field(
        default=None,
        description=(
            "按完成状态过滤：true 只看已完成，false 只看未完成。"
            "不传则返回所有状态。"
        ),
    )
    keyword: str | None = Field(
        default=None,
        description="按任务标题关键字模糊搜索。不传则不做关键字过滤。",
    )
    page: int = Field(
        default=1,
        ge=1,
        description="页码，从 1 开始。",
    )
    page_size: int = Field(
        default=10,
        ge=1,
        le=100,
        description="每页数量，默认 10，最大 100。",
    )


class CreateTaskInput(BaseModel):
    """创建任务的参数"""
    title: str = Field(
        ...,
        description="任务标题，简洁明了，不超过 50 字。",
    )
    description: str | None = Field(
        default=None,
        description="任务详细描述，可选。",
    )
    priority: Literal["low", "medium", "high"] = Field(
        default="medium",
        description="优先级：low(低)、medium(中)、high(高)。默认 medium。",
    )
    deadline: date | None = Field(
        default=None,
        description="截止日期，格式为 YYYY-MM-DD，例如 2026-09-01。不传则无截止日期。",
    )


class UpdateTaskInput(BaseModel):
    """修改任务的参数：只修改传入的字段，其余保持不变"""
    task_id: int = Field(
        ...,
        description="要修改的任务的 ID 编号，必须是数字。",
    )
    title: str | None = Field(
        default=None,
        description="新的任务标题。不传则不修改。",
    )
    description: str | None = Field(
        default=None,
        description="新的任务描述。不传则不修改。",
    )
    done: bool | None = Field(
        default=None,
        description="新的完成状态：true 已完成，false 未完成。不传则不修改。",
    )
    priority: Literal["low", "medium", "high"] | None = Field(
        default=None,
        description="新的优先级：low、medium、high。不传则不修改。",
    )
    deadline: date | None = Field(
        default=None,
        description="新的截止日期，格式为 YYYY-MM-DD。不传则不修改。",
    )


class DeleteTaskInput(BaseModel):
    """删除任务的参数"""
    task_id: int = Field(
        ...,
        description="要删除的任务的 ID 编号，必须是数字。",
    )


class SearchKnowledgeInput(BaseModel):
    """知识库检索的参数"""
    query: str = Field(
        ...,
        min_length=1,
        description=(
            "用于检索知识库的问题或关键词。"
            "把用户的完整问题写进来，不要只写单个词。"
        ),
    )
    top_k: int = Field(
        default=3,
        ge=1,
        le=10,
        description="返回多少个最相关的文档片段，默认 3，最多 10。",
    )


# ============================================================
# 2. 序列化辅助
#    查询结果可能是缓存(dict)或数据库对象(Task)，
#    统一转成 LLM 能读懂的 dict，再由调用方喂回模型。
# ============================================================

def _task_to_dict(task: Task | dict) -> dict:
    if isinstance(task, dict):
        return task
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "done": task.done,
        "priority": task.priority,
        "deadline": (
            task.deadline.isoformat()
            if task.deadline
            else None
        ),
    }


# ============================================================
# 3. 工具工厂
#    Tool 的函数要访问数据库，但 session 和当前登录用户都是
#    "每请求"才有的值，所以不能定义成模块级工具，而是每次请求
#    用闭包把 session / owner_id 绑定进去。函数体只做参数转发，
#    并捕获业务异常转成给 LLM 的错误信息，不抛异常炸掉对话。
# ============================================================

def build_task_tools(
    *,
    session: Session,
    owner_id: int,
    access_token: str | None = None,
    docmind_client: DocMindClient | None = None,
) -> list[StructuredTool]:
    """把 TaskService 的业务能力包装成绑定当前会话的工具列表。

    access_token 不为空时，额外挂上 search_knowledge 工具：它通过 HTTP
    调 DocMind 的纯检索接口，token 原样透传以维持用户隔离。
    docmind_client 只用于测试注入，运行时按配置新建。
    """

    def get_tasks(
        done: bool | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 10,
    ) -> dict:
        result = task_service.get_tasks(
            session=session,
            owner_id=owner_id,
            page=page,
            page_size=page_size,
            keyword=keyword,
            done=done,
        )
        return {
            "success": True,
            "page": result["page"],
            "page_size": result["page_size"],
            "total": result["total"],
            "tasks": [
                _task_to_dict(task)
                for task in result["tasks"]
            ],
        }

    def create_task(
        title: str,
        description: str | None = None,
        priority: str = "medium",
        deadline: date | None = None,
    ) -> dict:
        try:
            task_in = TaskCreate(
                title=title,
                description=description,
                priority=priority,
                deadline=deadline,
            )
            task = task_service.create_task(
                session=session,
                task=task_in,
                owner_id=owner_id,
            )
            return {
                "success": True,
                "task": _task_to_dict(task),
            }
        except ValidationError as exc:
            return {
                "success": False,
                "error": f"invalid task arguments: {exc.errors()}",
            }

    def update_task(
        task_id: int,
        title: str | None = None,
        description: str | None = None,
        done: bool | None = None,
        priority: str | None = None,
        deadline: date | None = None,
    ) -> dict:
        # StructuredTool 会把 schema 里所有字段(含默认值)都传给函数，
        # 所以"没传"和"传了 null"在这里都是 None。
        # 先过滤掉 None 再构造 TaskPatch，否则 model_dump(exclude_unset)
        # 会把没动的字段也当成显式设置为 null 而清空。
        updates = {
            "title": title,
            "description": description,
            "done": done,
            "priority": priority,
            "deadline": deadline,
        }
        updates = {
            key: value
            for key, value in updates.items()
            if value is not None
        }
        if not updates:
            return {
                "success": False,
                "error": "no fields to update",
            }

        try:
            task = task_service.partial_update_task(
                session=session,
                task_id=task_id,
                task=TaskPatch(**updates),
                owner_id=owner_id,
            )
            return {
                "success": True,
                "task": _task_to_dict(task),
            }
        except TaskNotFoundError:
            return {
                "success": False,
                "error": f"task {task_id} not found",
            }
        except TaskForbiddenError:
            return {
                "success": False,
                "error": (
                    f"task {task_id} does not belong to current user"
                ),
            }
        except ValidationError as exc:
            return {
                "success": False,
                "error": f"invalid task arguments: {exc.errors()}",
            }

    def delete_task(
        task_id: int,
    ) -> dict:
        try:
            task_service.delete_task(
                session=session,
                task_id=task_id,
                owner_id=owner_id,
            )
            return {
                "success": True,
                "message": f"task {task_id} deleted",
            }
        except TaskNotFoundError:
            return {
                "success": False,
                "error": f"task {task_id} not found",
            }
        except TaskForbiddenError:
            return {
                "success": False,
                "error": (
                    f"task {task_id} does not belong to current user"
                ),
            }

    get_tasks_tool = StructuredTool.from_function(
        func=get_tasks,
        name="get_tasks",
        description=(
            "查询当前登录用户的任务列表，"
            "可按完成状态(done)和标题关键字过滤，支持分页。"
        ),
        args_schema=GetTasksInput,
    )

    create_task_tool = StructuredTool.from_function(
        func=create_task,
        name="create_task",
        description=(
            "为当前登录用户创建一个新的任务。"
            "当用户明确要求创建、新增、记录一个任务时使用。"
        ),
        args_schema=CreateTaskInput,
    )

    update_task_tool = StructuredTool.from_function(
        func=update_task,
        name="update_task",
        description=(
            "修改当前登录用户的某个任务，只更新传入的字段。"
            "当用户要求修改、更新任务时使用。"
        ),
        args_schema=UpdateTaskInput,#指定参数的模式（schema）为 UpdateTaskInput。
        # 这个 UpdateTaskInput 通常是一个 Pydantic 模型，定义了工具接受哪些参数、参数类型及是否必填等信息。
    )
    #创建一个 StructuredTool 实例，并将其赋值给变量 update_task_tool
    delete_task_tool = StructuredTool.from_function(
        func=delete_task,
        name="delete_task",
        description=(
            "删除当前登录用户的某个任务。"
            "当用户明确要求删除任务时使用。"
        ),
        args_schema=DeleteTaskInput,
    )

    tools = [
        get_tasks_tool,
        create_task_tool,
        update_task_tool,
        delete_task_tool,
    ]

    # 知识库工具依赖用户 token，只有调用方提供了才挂上；
    # 这样内部调用（例如只测任务 CRUD）不会被迫依赖 DocMind。
    if access_token:
        client = docmind_client or DocMindClient()

        async def search_knowledge(
            query: str,
            top_k: int = 3,
        ) -> dict:
            """检索企业知识库，返回相关文档片段（不含生成答案）。"""
            try:
                results = await client.search(
                    query=query,
                    token=access_token,
                    top_k=top_k,
                )
            except DocMindUnavailableError as exc:
                # 和任务工具一致：错误转成 dict 喂回模型，不炸掉整轮对话。
                return {
                    "success": False,
                    "error": f"knowledge base unavailable: {exc}",
                }

            if not results:
                return {
                    "success": True,
                    "results": [],
                    "message": "知识库中没有找到相关内容。",
                }

            return {
                "success": True,
                "results": results,
            }

        tools.append(
            StructuredTool.from_function(
                coroutine=search_knowledge,
                name="search_knowledge",
                description=(
                    "搜索企业知识库，返回与查询最相关的文档片段。"
                    "当用户的问题需要公司内部资料（制度、流程、文档）"
                    "支撑时使用。回答时应注明来源文件名。"
                ),
                args_schema=SearchKnowledgeInput,
            )
        )

    return tools


# ============================================================
# 4. 兼容层(旧 OpenAI dict 格式)
#    现有 chat_with_tools 仍走 llm_client.chat(tools=...)，
#    它只接受 OpenAI 协议的 dict，不认识 StructuredTool。
#    等后续把执行链路迁到 StructuredTool 后，这一层可以删掉。
# ============================================================

CREATE_TASK_TOOL = {
    "type": "function",
    "function": {
        "name": "create_task",
        "description": (
            "为当前登录用户创建一个新的任务。"
            "当用户明确要求创建、新增、记录一个任务时使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "任务标题",
                },
                "description": {
                    "type": ["string", "null"],
                    "description": "任务描述，没有则为 null",
                },
                "priority": {
                    "type": "string",
                    "enum": [
                        "low",
                        "medium",
                        "high",
                    ],
                    "description": "任务优先级",
                },
                "deadline": {
                    "type": ["string", "null"],
                    "description": (
                        "任务截止日期，格式 YYYY-MM-DD；"
                        "无法确定时为 null"
                    ),
                },
            },
            "required": [
                "title",
                "priority",
            ],
            "additionalProperties": False,
        },
    },
}


TASKFLOW_TOOLS = [
    CREATE_TASK_TOOL,
]