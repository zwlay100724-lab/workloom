"""对话会话 CRUD。

多轮问答需要先创建 conversation，再把 conversation_id 传给 /rag/query。
所有接口都要求 JWT，归属校验在 ConversationService 里完成。
"""
from fastapi import APIRouter, Depends, Query, status
from shared.db.database import get_session
from sqlmodel import Session

from app.api.deps import CurrentUserIdDep
from app.schemas.conversation import (
    ConversationCreate,
    ConversationListResponse,
    ConversationMessageListResponse,
    ConversationRead,
)
from app.services.conversation_service import ConversationService

router = APIRouter(prefix="/conversations", tags=["对话"])


@router.post(
    "",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(
    request: ConversationCreate,
    current_user_id: CurrentUserIdDep,
    session: Session = Depends(get_session),
):
    return ConversationService(session).create_conversation(
        user_id=current_user_id,
        title=request.title,
    )


@router.get("", response_model=ConversationListResponse)
def list_conversations(
    current_user_id: CurrentUserIdDep,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session),
):
    items, total = ConversationService(session).list_conversations(
        user_id=current_user_id,
        skip=skip,
        limit=limit,
    )
    return ConversationListResponse(items=items, total=total)


@router.get(
    "/{conversation_id}/messages",
    response_model=ConversationMessageListResponse,
)
def list_messages(
    conversation_id: int,
    current_user_id: CurrentUserIdDep,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
):
    items, total = ConversationService(session).list_messages(
        conversation_id=conversation_id,
        user_id=current_user_id,
        skip=skip,
        limit=limit,
    )
    return ConversationMessageListResponse(items=items, total=total)


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_conversation(
    conversation_id: int,
    current_user_id: CurrentUserIdDep,
    session: Session = Depends(get_session),
):
    ConversationService(session).delete_conversation(
        conversation_id=conversation_id,
        user_id=current_user_id,
    )
