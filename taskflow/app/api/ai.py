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
    ApplyGeneratedTasksRequest,
    GenerateTasksPreviewResponse,
    GenerateTasksRequest,
    GenerateTasksResponse,
    PreviewTask,
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

# 预览里每条任务附带的引用摘要长度上限。
SOURCE_SNIPPET_MAX_CHARS = 200


async def _get_indexed_document(
    *,
    docmind: DocMindClient,
    token: str,
    document_id: int,
) -> dict:
    """取文档并确认已完成索引；把下游错误统一转成 HTTP 语义。"""
    try:
        document = await docmind.get_document(
            document_id=document_id,
            token=token,
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
    return document


@router.post(
    "/tasks/from-document/preview",
    response_model=GenerateTasksPreviewResponse,
)
async def preview_tasks_from_document(
    payload: GenerateTasksRequest,
    current_user: CurrentUserDep,
    access_token: AccessTokenDep,
    llm_service: LLMServiceDep,
    docmind: Annotated[DocMindClient, Depends(get_docmind_client)],
) -> GenerateTasksPreviewResponse:
    """第一步：从文档提取任务建议，**不写库**。

    返回的每条任务都带引用片段的摘要，用户可以在前端编辑
    （改标题 / 优先级 / 截止日期，或删掉不需要的）后再调用 apply。
    """
    document = await _get_indexed_document(
        docmind=docmind,
        token=access_token,
        document_id=payload.document_id,
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
    except DocMindDocumentNotFoundError as exc:
        # get_document 和 search 之间文档被删除。
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="document not found",
        ) from exc
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

    chunk_by_index = {
        chunk["chunk_index"]: chunk
        for chunk in chunks
    }
    preview_tasks: list[PreviewTask] = []
    for spec in specs:
        snippet = None
        if spec.source_chunk_index is not None:
            chunk = chunk_by_index.get(spec.source_chunk_index)
            if chunk is not None:
                snippet = chunk["content"][:SOURCE_SNIPPET_MAX_CHARS]
        preview_tasks.append(
            PreviewTask(
                **spec.model_dump(),
                source_snippet=snippet,
            )
        )

    return GenerateTasksPreviewResponse(
        document_id=payload.document_id,
        filename=filename,
        instruction=payload.instruction,
        tasks=preview_tasks,
    )


@router.post(
    "/tasks/from-document/apply",
    response_model=GenerateTasksResponse,
)
async def apply_generated_tasks(
    payload: ApplyGeneratedTasksRequest,
    current_user: CurrentUserDep,
    access_token: AccessTokenDep,
    session: SessionDep,
    docmind: Annotated[DocMindClient, Depends(get_docmind_client)],
) -> GenerateTasksResponse:
    """第二步：用户确认后，把编辑过的任务落库。

    内容以请求体为准（用户可能改过标题 / 优先级），服务端只强制
    document_id / filename 来自 DocMind 的校验结果，不信任客户端传的归属。
    """
    document = await _get_indexed_document(
        docmind=docmind,
        token=access_token,
        document_id=payload.document_id,
    )
    filename = document.get("filename")

    tasks = task_service.create_generated_tasks(
        session=session,
        owner_id=current_user.id,
        specs=payload.tasks,
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