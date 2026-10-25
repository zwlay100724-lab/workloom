from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUserDep
from app.schemas.conversation import (
    ConversationChatRequest,
    ConversationChatResponse,
    ConversationCreate,
    ConversationRead,
)
from app.services.conversation_service import ConversationServiceDep
from app.services.llm_service import LLMServiceDep

router = APIRouter(
    prefix="/ai/conversations",
    tags=["AI Conversations"],
)

@router.post(
    "",
    response_model=ConversationRead,
)
async def create_conversation(
    request: ConversationCreate,
    current_user: CurrentUserDep,
    service: ConversationServiceDep,
):
    user_id = current_user.id
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User record has no id",
        )
    return await service.create(
        user_id=user_id,
        title=request.title,
    )

@router.post(
    "/{conversation_id}/chat",
    response_model=ConversationChatResponse,
)
async def chat_with_history(
    conversation_id: int,
    request: ConversationChatRequest,
    current_user: CurrentUserDep,
    llm_service: LLMServiceDep,
):
    user_id = current_user.id
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User record has no id",
        )
    answer = await llm_service.chat_with_history(
        conversation_id=conversation_id,
        user_id=user_id,
        message=request.message,
    )
    return ConversationChatResponse(
        conversation_id=conversation_id,
        answer=answer,
    )