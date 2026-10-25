import asyncio
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from shared.exceptions import (
    LLMError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)

# 使用你第一阶段已经存在的 CurrentUserDep。
# 如果你的实际路径不是 app.api.deps，
# 保留你项目原来的 import 路径即可。
from app.api.deps import AccessTokenDep, CurrentUserDep, SessionDep
from app.clients.docmind import (
    DocMindClient,
    DocMindDocumentNotFoundError,
    DocMindUnavailableError,
    get_docmind_client,
)
from app.schemas.ai import (
    ChatRequest,
    ChatResponse,
    PlanRequest,
    StructuredTaskRequest,
    StructuredTaskResponse,
    TaskIntent,
    TaskPlan,
    ToolChatRequest,
    ToolChatResponse,
)
from app.schemas.generated import (
    GenerateTasksRequest,
    GenerateTasksResponse,
)
from app.schemas.task import TaskResponse
from app.services import task_service
from app.services.llm_service import LLMServiceDep

router = APIRouter(
    prefix="/ai",
    tags=["AI"],
)

logger = logging.getLogger(__name__)


def _sse_error(code: str, message: str) -> str:
    payload = {"error": {"code": code, "message": message}}
    return (
        "event: error\n"
        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    )

#请求，达模型服务，返回达模型回答：答案等于达模型服务的结果，返回结果进行规范化处理
@router.post("/chat",response_model=ChatResponse,)
async def chat(
    request: ChatRequest,
    current_user: CurrentUserDep,
    llm_service: LLMServiceDep,
) -> ChatResponse:
    user_id = current_user.id
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User record has no id",
        )
    answer = await llm_service.chat(
        message=request.message,
        user_id=user_id,
    )
    return ChatResponse(answer=answer)

@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    current_user: CurrentUserDep,
    llm_service: LLMServiceDep,
):
    user_id = current_user.id
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User record has no id",
        )

#把 AI 逐字生成的碎片，实时打包成前端能听懂的标准快递包裹，一件件发出去，最后发一个“包裹已送完”的终止信号。
    async def event_generator():
        try:
            async for chunk in llm_service.stream_chat(
                message=request.message,
                user_id=user_id,
            ):
                data = json.dumps(
                    {"delta": chunk},
                    ensure_ascii=False,
                )
                yield f"data: {data}\n\n"

            yield "data: [DONE]\n\n"

        except asyncio.CancelledError:
            # 客户端断开：不要假装成功，也不要再写流；让任务正常取消。
            raise
        except LLMTimeoutError:
            logger.warning(
                "chat stream timeout user_id=%s",
                user_id,
            )
            yield _sse_error("LLM_TIMEOUT", "AI service timed out")
        except LLMRateLimitError:
            logger.warning(
                "chat stream rate limited user_id=%s",
                user_id,
            )
            yield _sse_error(
                "LLM_RATE_LIMIT",
                "AI service rate limit exceeded",
            )
        except LLMServiceUnavailableError:
            logger.warning(
                "chat stream unavailable user_id=%s",
                user_id,
            )
            yield _sse_error(
                "LLM_UNAVAILABLE",
                "AI service is temporarily unavailable",
            )
        except LLMError:
            # 不把底层 SDK 错误文本发给客户端。
            logger.warning(
                "chat stream llm error user_id=%s",
                user_id,
                exc_info=True,
            )
            yield _sse_error("LLM_ERROR", "AI service failed")
        except Exception:
            logger.exception(
                "chat stream unexpected error user_id=%s",
                user_id,
            )
            yield _sse_error(
                "INTERNAL_ERROR",
                "AI service failed",
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

@router.post(
    "/chat/structured",
    response_model=StructuredTaskResponse,
)
async def structured_chat(
    payload: StructuredTaskRequest,
    current_user: CurrentUserDep,
    llm_service: LLMServiceDep,
) -> StructuredTaskResponse:

    data, _usage = await llm_service.extract_task(
        message=payload.message,
        user_id=current_user.id,
    )

    return StructuredTaskResponse(
        data=data,
    )

@router.post(
    "/chat/tools",
    response_model=ToolChatResponse,
)
async def chat_with_tools(
    payload: ToolChatRequest,
    current_user: CurrentUserDep,
    access_token: AccessTokenDep,
    llm_service: LLMServiceDep,
) -> ToolChatResponse:

    answer = await llm_service.chat_with_tools(
        message=payload.message,
        user_id=current_user.id,
        # 原样透传给 DocMind：知识库检索工具需要它来维持用户身份。
        access_token=access_token,
    )

    return ToolChatResponse(
        answer=answer,
    )

@router.post(
    "/plan",
    response_model=TaskPlan,
)
async def plan_tasks(
    payload: PlanRequest,
    current_user: CurrentUserDep,
    llm_service: LLMServiceDep,
) -> TaskPlan:
    """
    AI 只生成任务计划，不修改数据库。
    """

    return await llm_service.plan_tasks(
        message=payload.message,
        user_id=current_user.id,
    )

@router.post(
    "/chat/intent",
    response_model=TaskIntent,
)
async def parse_intent(
    request: StructuredTaskRequest,
    current_user: CurrentUserDep,
    llm_service: LLMServiceDep,
) -> TaskIntent:
    """
    将用户自然语言解析成结构化 TaskIntent(PydanticOutputParser 版)。
    """

    user_id = current_user.id
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User record has no id",
        )

    return await llm_service.parse_task_intent(
        user_input=request.message,
        user_id=user_id,
    )


@router.post(
    "/chat/intent/structured",
    response_model=TaskIntent,
)
async def parse_intent_structured(
    request: StructuredTaskRequest,
    current_user: CurrentUserDep,
    llm_service: LLMServiceDep,
) -> TaskIntent:
    """
    将用户自然语言解析成结构化 TaskIntent(with_structured_output 版)。
    """

    user_id = current_user.id
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User record has no id",
        )

    return await llm_service.parse_task_intent_structured(
        user_input=request.message,
        user_id=user_id,
    )


# 没传 instruction 时用它做检索 query：把行动项类片段召回出来。
DEFAULT_TASK_EXTRACTION_QUERY = (
    "提取文档中的行动项、待办事项、责任人、截止时间和交付要求"
)


@router.post(
    "/tasks/from-document",
    response_model=GenerateTasksResponse,
)
async def generate_tasks_from_document(
    payload: GenerateTasksRequest,
    current_user: CurrentUserDep,
    access_token: AccessTokenDep,
    session: SessionDep,
    llm_service: LLMServiceDep,
    docmind: Annotated[DocMindClient, Depends(get_docmind_client)],
) -> GenerateTasksResponse:
    """从 DocMind 文档生成任务，并写入来源引用。

    完整闭环：
      1. 校验文档归属（DocMind 404 即不存在或不是你的）；
      2. 在该文档范围内检索行动项相关片段；
      3. LLM 结构化提取任务（每条的 source_chunk_index 可追溯）；
      4. 一次性写入任务 + task_documents 关联，全部带来源文件引用。
    """
    try:
        document = await docmind.get_document(
            document_id=payload.document_id,
            token=access_token,
        )
    except DocMindDocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="document not found",
        ) from exc
    except DocMindUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="knowledge service unavailable",
        ) from exc

    # 还在解析 / 索引中的文档没有可用片段，先拒绝而不是生成空任务。
    if document.get("status") != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="document is not indexed yet",
        )

    filename = document.get("filename")
    query = payload.instruction or DEFAULT_TASK_EXTRACTION_QUERY

    try:
        chunks = await docmind.search(
            query=query,
            token=access_token,
            top_k=20,
            document_id=payload.document_id,
        )
    except DocMindUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="knowledge service unavailable",
        ) from exc

    if not chunks:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="document has no indexed content",
        )

    specs = await llm_service.extract_tasks_from_document(
        filename=filename or f"document-{payload.document_id}",
        chunks=chunks,
        user_id=current_user.id,
        instruction=payload.instruction,
        max_tasks=payload.max_tasks,
    )

    tasks = task_service.create_generated_tasks(
        session=session,
        owner_id=current_user.id,
        specs=specs,
        document_id=payload.document_id,
        filename=filename,
    )

    return GenerateTasksResponse(
        document_id=payload.document_id,
        filename=filename,
        tasks=[
            TaskResponse.model_validate(task)
            for task in tasks
        ],
    )