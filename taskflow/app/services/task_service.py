from app.core.exceptions import TaskForbiddenError, TaskNotFoundError
from app.models.task import Task
from app.repositories import task_cache_repository, task_repository
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
    task_repository.delete_task(session, db_task)
    session.commit()
    task_cache_repository.invalidate_task_list_cache(
        owner_id=owner_id
    )
    return True

