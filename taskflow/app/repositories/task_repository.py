from app.models.task import Task
from sqlalchemy import func
from sqlmodel import Session, select


def get_tasks(session: Session,page: int,page_size: int,owner_id: int,keyword: str | None = None,done: bool | None = None,):
    statement = select(Task)
    if done is not None:
        statement = statement.where(Task.done == done )#where筛选查询条件
    if keyword is not None:
        statement = statement.where(Task.title.ilike(f"%{keyword}%"))
    statement = statement.where(Task.owner_id == owner_id)
    statement = statement.order_by(Task.id.asc() )#先筛选后排序
    offset = (page - 1) * page_size
    statement = (statement.offset(offset).limit(page_size) )
    return session.exec(statement).all()

def count_tasks(session: Session,owner_id: int,keyword: str | None = None,done: bool | None = None):
    statement = select(func.count()).select_from(Task)
    if done is not None:
        statement = statement.where(Task.done == done)
    if keyword is not None:
        statement = statement.where(Task.title.ilike(f"%{keyword}%"))
    statement = statement.where(Task.owner_id == owner_id)
    return session.exec(statement).one()#得到满足搜索和过滤条件的全部记录数
#len(tasks)表示当前这一页有多少条，total表示符合查询条件的一共有多少条，这样前端才能够判断有多少页

def get_task_by_id(session: Session,task_id: int,):
    return session.get(
        Task,
        task_id,
    )

def add_task(session: Session,task: Task,):
    session.add(task)

def delete_task(session: Session, task: Task,):
    session.delete(task)

