from fastapi import APIRouter, Depends
from shared.db.database import get_session
from sqlmodel import Session

from app.api.deps import CurrentUserIdDep
from app.schemas.rag import RAGQueryRequest, RAGQueryResponse
from app.services.document_service import DocumentService
from app.services.rag_service import RAGService

router = APIRouter(prefix="/rag", tags=["RAG 问答"])


@router.post("/query", response_model=RAGQueryResponse)
def rag_query(
        request: RAGQueryRequest,
        current_user_id: CurrentUserIdDep,
        session: Session = Depends(get_session),
):
    """
    RAG 问答接口：基于知识库回答用户问题。

    完整流程：问题 → 向量化 → 检索 Top-K → 拼接 Prompt → LLM 生成回答
    """
    # 指定了 document_id 时先校验归属；否则检索 SQL 里的 user_id 过滤
    # 仍然是兜底，不会读到别人的片段。
    if request.document_id is not None:
        DocumentService(session).get_owned_document(
            request.document_id,
            current_user_id,
        )

    rag_service = RAGService(session)
    result = rag_service.query(
        question=request.question,
        user_id=current_user_id,
        document_id=request.document_id,
        top_k=request.top_k,
        conversation_id=request.conversation_id,
    )
    return RAGQueryResponse(
        answer=result["answer"],
        sources=result["sources"],
    )