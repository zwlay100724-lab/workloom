"""纯检索 API：只返回命中的文档片段，不调用 LLM。

为什么要单独开一个接口，而不是复用 /rag/query：
- /rag/query 会走完整的"检索 → 拼 prompt → LLM 生成"，一次几百毫秒到几秒，
  还会产生费用；
- "任务详情的相关知识"、"工作台聚合搜索"这类场景只需要片段本身，
  由调用方决定怎么展示 / 怎么用，不该被迫付一次 LLM 的账。

归属过滤和 /rag/query 完全一致：只信服务端从 JWT 解析出的 user_id，
指定 document_id 时先校验文档属于当前用户，检索 SQL 里再兜底过滤一次。
"""
from typing import Annotated

from fastapi import APIRouter, Depends
from shared.db.database import get_session
from sqlmodel import Session, select

from app.api.deps import CurrentUserIdDep
from app.models.document import Document
from app.schemas.retrieval import (
    RetrievalSearchRequest,
    RetrievalSearchResponse,
    RetrievedChunk,
)
from app.services.document_service import DocumentService
from app.services.retrieval_service import RetrievalService

router = APIRouter(prefix="/retrieval", tags=["检索"])

SessionDep = Annotated[Session, Depends(get_session)]


def get_retrieval_service() -> RetrievalService:
    """依赖工厂：测试可以覆盖它，避免在单测里构造真实 Embedding / LLM 客户端。"""
    return RetrievalService()


RetrievalServiceDep = Annotated[
    RetrievalService,
    Depends(get_retrieval_service),
]


def _load_filenames(
        session: Session,
        user_id: int,
        document_ids: set[int],
) -> dict[int, str]:
    """一次批量取回片段所属文档的文件名，避免逐条查询造成 N+1。"""
    if not document_ids:
        return {}
    documents = session.exec(
        select(Document).where(
            Document.id.in_(document_ids),
            # 再按 user_id 过滤一次：检索和取文件名之间文档可能已被删除 / 转移。
            Document.user_id == user_id,
        )
    ).all()
    return {
        document.id: document.filename
        for document in documents
    }


def _score(metadata: dict) -> float:
    """vector 模式取 similarity，hybrid 模式取 rrf_score。"""
    if "similarity" in metadata:
        return round(float(metadata["similarity"]), 6)
    return round(float(metadata.get("rrf_score", 0.0)), 6)


@router.post("/search", response_model=RetrievalSearchResponse)
def search(
        request: RetrievalSearchRequest,
        current_user_id: CurrentUserIdDep,
        service: RetrievalServiceDep,
        session: SessionDep,
):
    # 指定了 document_id 时先校验归属；检索 SQL 里的 user_id 过滤仍是兜底。
    if request.document_id is not None:
        DocumentService(session).get_owned_document(
            request.document_id,
            current_user_id,
        )

    retrieve = (
        service.retrieve_hybrid
        if request.mode == "hybrid"
        else service.retrieve
    )
    documents = retrieve(
        session,
        request.query,
        user_id=current_user_id,
        top_k=request.top_k,
        document_id=request.document_id,
    )

    document_ids = {
        document.metadata["document_id"]
        for document in documents
        if document.metadata.get("document_id") is not None
    }
    filenames = _load_filenames(session, current_user_id, document_ids)

    results = [
        RetrievedChunk(
            document_id=document.metadata["document_id"],
            filename=filenames.get(document.metadata["document_id"]),
            chunk_index=document.metadata.get("chunk_index", 0),
            page=document.metadata.get("page"),
            content=document.page_content,
            score=_score(document.metadata),
        )
        for document in documents
        if document.metadata.get("document_id") is not None
    ]

    return RetrievalSearchResponse(
        query=request.query,
        mode=request.mode,
        results=results,
    )
