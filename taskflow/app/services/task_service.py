from datetime import datetime

from app.core.exceptions import TaskForbiddenError, TaskNotFoundError
from app.models.task import Task
from app.models.task_document import TaskDocument
from app.repositories import (
    task_cache_repository,
    task_document_repository,
    task_repository,
)
from app.schemas.generated import GeneratedTaskSpec
from app.schemas.task import (
    TaskCreate,
    TaskPatch,
    TaskUpdate,
)
from sqlmodel import Session


def _get_owned_task_or_raise(session: Session,task_id: int,owner_id: int,) -> Task:
    if (
        task_cache_repository
        .is_missing_task_cached(
            task_id
        )
    ):
        raise TaskNotFoundError()
    task = task_repository.get_task_by_id(session=session,task_id=task_id,)
    if task is None:
        task_cache_repository.cache_missing_task(task_id)
        raise TaskNotFoundError()
    if task.owner_id != owner_id:
        raise TaskForbiddenError()
    return task
#函数前面_表示这是 task_service.py 内部辅助函数，不是给 Router 直接调用的公共业务接口

def get_tasks(session: Session,owner_id: int,page: int,page_size: int,keyword: str | None = None,done: bool | None = None,):
    cached_result = (
        task_cache_repository
        .get_task_list_cache(
            owner_id=owner_id,
            page=page,
            page_size=page_size,
            keyword=keyword,
            done=done,
        )
    )
    if cached_result is not None:
        return cached_result
    tasks = task_repository.get_tasks(session=session,page=page,page_size=page_size,keyword=keyword,done=done,owner_id=owner_id,)
    total = task_repository.count_tasks( session=session,keyword=keyword,done=done,owner_id=owner_id,)
    result = {"page": page,"page_size": page_size,"total": total,"tasks": tasks, }
    task_cache_repository.set_task_list_cache(
        owner_id=owner_id,
        page=page,
        page_size=page_size,
        keyword=keyword,
        done=done,
        result=result,
    )
    return result

#repository做完查询当页，统计满足条件总数，然后交给service将两次数据库结果组装成分页业务处理

def get_task(session: Session, task_id: int, owner_id: int):
    return _get_owned_task_or_raise(session, task_id, owner_id)

def create_task(session: Session, task: TaskCreate, owner_id: int):
    db_task=Task(**task.model_dump(),owner_id=owner_id,)
    task_repository.add_task(session, db_task)
    session.commit()#为什么把 commit 放在 Service，而不是 Repository？
    session.refresh(db_task)
    # 用数据库生成的主键，不能用请求对象 task（它现在已经没有 id 字段）。
    task_cache_repository.clear_missing_task_cache(db_task.id)
    task_cache_repository.invalidate_task_list_cache(
        owner_id=owner_id
    )
    return db_task

def update_task(session: Session, task_id: int, task: TaskUpdate, owner_id: int):
    db_task=_get_owned_task_or_raise(session, task_id, owner_id)
    if db_task is None:
        return None
    db_task.title = task.title
    db_task.description = task.description
    db_task.done = task.done
    db_task.priority = task.priority
    db_task.deadline = task.deadline
    task_repository.add_task(session, db_task)
    session.commit()
    session.refresh(db_task)
    task_cache_repository.invalidate_task_list_cache(
        owner_id=owner_id
    )
    return db_task

def partial_update_task(session: Session, task_id: int, task: TaskPatch, owner_id: int):
    db_task=_get_owned_task_or_raise(session, task_id, owner_id)#调用仓库层的 get_task_by_id 从数据库获取当前任务实例
    if db_task is None:
        return None
    update_data=task.model_dump(
        exclude_unset=True
    )
    for field,value in update_data.items():
        setattr(db_task, field, value)
    #使用 setattr 将更新值逐一设置到 db_task 对象的对应属性上。
    # 这样，只有客户端传入的字段会被修改，其他字段保持不变。
    task_repository.add_task(session, db_task)
    session.commit()
    session.refresh(db_task)
    task_cache_repository.invalidate_task_list_cache(
        owner_id=owner_id
    )
    return db_task

def delete_task(session: Session, task_id: int, owner_id: int):
    db_task=_get_owned_task_or_raise(session, task_id, owner_id)
    if db_task is None:
        return False
    # 先删关联：task_documents.task_id 有外键，Postgres 下不先删会被拒绝。
    task_document_repository.delete_links_for_task(session, task_id)
    task_repository.delete_task(session, db_task)
    session.commit()
    task_cache_repository.invalidate_task_list_cache(
        owner_id=owner_id
    )
    return True


# ── 任务 ↔ 文档关联 ──────────────────────────────────────────

def list_task_documents(
        session: Session,
        task_id: int,
        owner_id: int,
) -> list[TaskDocument]:
    _get_owned_task_or_raise(session, task_id, owner_id)
    return task_document_repository.list_for_task(session, task_id)


def link_document(
        session: Session,
        *,
        task_id: int,
        owner_id: int,
        document_id: int,
        filename: str | None,
        index_version: int = 0,
) -> TaskDocument:
    """关联文档；重复调用是幂等的，并刷新文件名/索引版本快照。

    用户重新关联代表当前文档可用：清掉失效标记，更新版本。
    """
    _get_owned_task_or_raise(session, task_id, owner_id)
    now = datetime.utcnow()

    link = task_document_repository.get_link(
        session,
        task_id,
        document_id,
    )
    if link is None:
        link = task_document_repository.add_link(
            session,
            task_id=task_id,
            document_id=document_id,
            filename=filename,
            index_version=index_version,
            last_event_at=now,
        )
        session.commit()
        session.refresh(link)
        return link

    # 用户重新关联代表当前状态有效：刷新快照、清失效，
    # 并把事件水位推到当前时间，迟到很久的旧删除/重建事件不会再生效。
    link.filename = filename if filename is not None else link.filename
    link.index_version = index_version
    link.invalidated_at = None
    link.invalid_reason = None
    link.last_event_at = now
    session.add(link)
    session.commit()
    session.refresh(link)
    return link


def invalidate_link(
        session: Session,
        *,
        task_id: int,
        owner_id: int,
        document_id: int,
        reason: str,
) -> None:
    """读路径兜底：DocMind 返回 404 时顺手标记关联失效。

    事件可能因为队列故障没到；用户打开任务时发现文档已不存在，
    这里 best-effort 修复，避免一直显示有效关联。
    """
    _get_owned_task_or_raise(session, task_id, owner_id)
    link = task_document_repository.get_link(
        session,
        task_id,
        document_id,
    )
    if link is None or link.invalidated_at is not None:
        return

    now = datetime.utcnow()
    link.invalidated_at = now
    link.invalid_reason = reason
    link.last_event_at = now
    session.add(link)
    session.commit()


def unlink_document(
        session: Session,
        *,
        task_id: int,
        owner_id: int,
        document_id: int,
) -> None:
    """解除关联；本来就没关联时也视为成功（幂等）。"""
    _get_owned_task_or_raise(session, task_id, owner_id)
    if task_document_repository.delete_link(
            session,
            task_id,
            document_id,
    ):
        session.commit()


def create_generated_tasks(
        session: Session,
        *,
        owner_id: int,
        specs: list[GeneratedTaskSpec],
        document_id: int,
        filename: str | None,
        index_version: int = 0,
) -> list[Task]:
    """把「文档 → 任务」的结果一次性落库。

    只在最后 commit 一次：要么任务和关联全部写入，要么整体回滚，
    不会留下"任务建了一半、引用缺失"的中间状态。
    """
    event_at = datetime.utcnow()
    tasks = [
        Task(
            title=spec.title,
            description=spec.description,
            priority=spec.priority,
            deadline=spec.deadline,
            owner_id=owner_id,
            source_document_id=document_id,
            source_filename=filename,
            source_chunk_index=spec.source_chunk_index,
            source_index_version=index_version,
            source_last_event_at=event_at,
        )
        for spec in specs
    ]
    if not tasks:
        return []

    for task in tasks:
        session.add(task)
    # flush 拿到自增主键，关联表才能引用；此时还没提交。
    session.flush()

    for task in tasks:
        task_document_repository.add_link(
            session,
            task_id=task.id,
            document_id=document_id,
            filename=filename,
            index_version=index_version,
            last_event_at=event_at,
        )

    session.commit()
    for task in tasks:
        session.refresh(task)
        task_cache_repository.clear_missing_task_cache(task.id)
    task_cache_repository.invalidate_task_list_cache(owner_id=owner_id)
    return tasks

