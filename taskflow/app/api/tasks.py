import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.api.deps import AccessTokenDep, CurrentUserDep, SessionDep
from app.clients.docmind import (
    DocMindClient,
    DocMindDocumentNotFoundError,
    DocMindUnavailableError,
    get_docmind_client,
)
from app.schemas.knowledge import KnowledgeChunk, TaskKnowledgeResponse
from app.schemas.task import TaskCreate, TaskPatch, TaskResponse, TaskUpdate
from app.schemas.task_document import (
    TaskDocumentLinkRequest,
    TaskDocumentListResponse,
    TaskDocumentResponse,
)
from app.services import task_service

router = APIRouter(prefix="/tasks", tags=["tasks"])

logger = logging.getLogger(__name__)

# 关联文档很多时，逐个检索的扇出要有限制，避免一次请求打爆 DocMind。
MAX_LINKED_DOCUMENTS_FOR_SEARCH = 10

@router.get("")
def get_tasks(
    session: SessionDep,
    current_user: CurrentUserDep,
    page:Annotated[int,Query(ge=1,le=100)]=1,
    page_size:Annotated[int,Query(ge=1,le=100)]=10,
    keyword:Annotated[str|None,Query(min_length=2,max_length=50)]=None,
    done:bool|None=None,
):
    return task_service.get_tasks(session=session,owner_id=current_user.id,page=page,page_size=page_size,keyword=keyword,done=done)

@router.get("/{task_id}")
def get_task(task_id: Annotated[int, Path(ge=1)], session: SessionDep, current_user: CurrentUserDep):
    return task_service.get_task(
        session=session,
        task_id=task_id,
        owner_id=current_user.id,
    )

@router.post("", response_model=TaskResponse,status_code=status.HTTP_201_CREATED)
def create_task(task: TaskCreate, session: SessionDep, current_user: CurrentUserDep):
    db_task = (
        task_service.create_task(session=session,task=task,owner_id=current_user.id,)
    )
    return db_task

#根据 URL 中传入的任务 ID，在内存列表 tasks 中查找并替换对应的任务数据。
# task: TaskUpdate：请求体参数，自动解析 JSON 并转换为 TaskUpdate 模型实例
@router.put("/{task_id}",response_model=TaskResponse)
def update_task(task_id: Annotated[int,Path(ge=1)],task: TaskUpdate, session: SessionDep, current_user: CurrentUserDep):
    return task_service.update_task(
        session=session,
        task_id=task_id,
        task=task,
        owner_id=current_user.id,
    )


@router.patch("/{task_id}",response_model=TaskResponse)
def partial_update_task(task_id: Annotated[int, Path(ge=1)],task: TaskPatch, session: SessionDep, current_user: CurrentUserDep):
    return task_service.partial_update_task(
        session=session,
        task_id=task_id,
        task=task,
        owner_id=current_user.id,
    )


@router.delete("/{task_id}",status_code=status.HTTP_204_NO_CONTENT,)
def delete_task(task_id: Annotated[int, Path(ge=1)], session: SessionDep, current_user: CurrentUserDep):
    return task_service.delete_task(
        session=session,
        task_id=task_id,
        owner_id=current_user.id,
    )


async def _search_linked_documents(
    *,
    docmind: DocMindClient,
    access_token: str,
    query: str,
    document_ids: list[int],
    top_k: int,
) -> list[dict]:
    """在多个关联文档里并行检索，再按分数合并。

    每条文档各查一次（DocMind 单次只接受一个 document_id），
    每路只取所需份额，避免 N 个文档时把 top_k 放大 N 倍。
    """
    per_document_k = max(1, top_k // len(document_ids) + 1)
    batches = await asyncio.gather(
        *(
            docmind.search(
                query=query,
                token=access_token,
                top_k=per_document_k,
                document_id=document_id,
            )
            for document_id in document_ids
        )
    )
    merged = [
        item
        for batch in batches
        for item in batch
    ]
    merged.sort(
        key=lambda item: item.get("score", 0.0),
        reverse=True,
    )
    return merged[:top_k]


@router.get("/{task_id}/knowledge", response_model=TaskKnowledgeResponse)
async def get_task_knowledge(
    task_id: Annotated[int, Path(ge=1)],
    session: SessionDep,
    current_user: CurrentUserDep,
    access_token: AccessTokenDep,
    docmind: Annotated[DocMindClient, Depends(get_docmind_client)],
    top_k: Annotated[int, Query(ge=1, le=20)] = 5,
    linked_only: Annotated[bool, Query()] = False,
):
    """任务详情「相关知识」：用标题 + 描述去知识库检索相关片段。

    只做检索、不生成答案（DocMind 的 /retrieval/search），所以延迟和
    费用都低；前端拿到片段后自己决定怎么展示。

    linked_only=true 时只在该任务显式关联的文档里检索——配合
    task_documents 关联使用，让「知识」范围可控。

    token 原样透传给 DocMind：它只验签，用户隔离仍由 uid 保证。
    """
    task = task_service.get_task(
        session=session,
        task_id=task_id,
        owner_id=current_user.id,
    )

    # 标题必填、描述可选；拼成一个查询串，描述为空时只用标题。
    query = " ".join(
        part
        for part in (task.title, task.description)
        if part
    ).strip()

    document_ids: list[int] = []
    if linked_only:
        links = task_service.list_task_documents(
            session=session,
            task_id=task_id,
            owner_id=current_user.id,
        )
        document_ids = [
            link.document_id
            for link in links
        ][:MAX_LINKED_DOCUMENTS_FOR_SEARCH]

        # 没有关联文档时不必再请求 DocMind。
        if not document_ids:
            return TaskKnowledgeResponse(
                task_id=task.id,
                query=query,
                results=[],
            )

    try:
        if linked_only:
            results = await _search_linked_documents(
                docmind=docmind,
                access_token=access_token,
                query=query,
                document_ids=document_ids,
                top_k=top_k,
            )
        else:
            results = await docmind.search(
                query=query,
                token=access_token,
                top_k=top_k,
            )
    except DocMindUnavailableError as exc:
        logger.warning(
            "task knowledge search failed task_id=%s error=%s",
            task_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="knowledge service unavailable",
        ) from exc

    return TaskKnowledgeResponse(
        task_id=task.id,
        query=query,
        results=[
            KnowledgeChunk.model_validate(item)
            for item in results
        ],
    )


# ── 任务 ↔ 文档关联 ──────────────────────────────────────────

@router.get(
    "/{task_id}/documents",
    response_model=TaskDocumentListResponse,
)
def list_task_documents(
    task_id: Annotated[int, Path(ge=1)],
    session: SessionDep,
    current_user: CurrentUserDep,
):
    links = task_service.list_task_documents(
        session=session,
        task_id=task_id,
        owner_id=current_user.id,
    )
    return TaskDocumentListResponse(
        task_id=task_id,
        items=[
            TaskDocumentResponse.model_validate(link)
            for link in links
        ],
    )


@router.post(
    "/{task_id}/documents",
    response_model=TaskDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def link_task_document(
    task_id: Annotated[int, Path(ge=1)],
    payload: TaskDocumentLinkRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
    access_token: AccessTokenDep,
    docmind: Annotated[DocMindClient, Depends(get_docmind_client)],
):
    """把一份 DocMind 文档关联到任务。

    先校验任务归属，再让 DocMind 校验文档归属：不存在的 id、
    别人的文档都会得到 404，不会在关联表里留下无效引用。
    重复关联是幂等的。
    """
    # 任务归属先校验：避免为不属于当前用户的任务去调用 DocMind。
    task_service.get_task(
        session=session,
        task_id=task_id,
        owner_id=current_user.id,
    )

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
        logger.warning(
            "link document failed task_id=%s error=%s",
            task_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="knowledge service unavailable",
        ) from exc

    link = task_service.link_document(
        session=session,
        task_id=task_id,
        owner_id=current_user.id,
        document_id=payload.document_id,
        filename=document.get("filename"),
    )
    return TaskDocumentResponse.model_validate(link)


@router.delete(
    "/{task_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def unlink_task_document(
    task_id: Annotated[int, Path(ge=1)],
    document_id: Annotated[int, Path(ge=1)],
    session: SessionDep,
    current_user: CurrentUserDep,
):
    """解除关联；本来就没有关联时同样返回 204（幂等）。"""
    task_service.unlink_document(
        session=session,
        task_id=task_id,
        owner_id=current_user.id,
        document_id=document_id,
    )

