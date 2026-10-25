import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, status
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
from app.api.deps import CurrentUserDep
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
    llm_service: LLMServiceDep,
) -> ToolChatResponse:

    answer = await llm_service.chat_with_tools(
        message=payload.message,
        user_id=current_user.id,
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