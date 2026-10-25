from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.api.deps import CurrentUserDep, SessionDep
from app.schemas.task import TaskCreate, TaskPatch, TaskResponse, TaskUpdate
from app.services import task_service

router = APIRouter(prefix="/tasks", tags=["tasks"])

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

