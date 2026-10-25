"""任务 ↔ 文档关联的持久化操作。

只做 SQL，不做归属校验和事务提交：
归属校验（任务是否属于当前用户）属于 service 的职责，
commit 由 service 在完整业务动作后统一执行。
"""
from app.models.task_document import TaskDocument
from sqlalchemy import delete
from sqlmodel import Session, select


def get_link(
        session: Session,
        task_id: int,
        document_id: int,
) -> TaskDocument | None:
    return session.exec(
        select(TaskDocument).where(
            TaskDocument.task_id == task_id,
            TaskDocument.document_id == document_id,
        )
    ).first()


def list_for_task(
        session: Session,
        task_id: int,
) -> list[TaskDocument]:
    return session.exec(
        select(TaskDocument)
        .where(TaskDocument.task_id == task_id)
        .order_by(TaskDocument.id.asc())
    ).all()


def add_link(
        session: Session,
        *,
        task_id: int,
        document_id: int,
        filename: str | None,
) -> TaskDocument:
    link = TaskDocument(
        task_id=task_id,
        document_id=document_id,
        filename=filename,
    )
    session.add(link)
    return link


def delete_link(
        session: Session,
        task_id: int,
        document_id: int,
) -> bool:
    """删除关联；返回是否真的删了一条（用于幂等语义）。"""
    link = get_link(session, task_id, document_id)
    if link is None:
        return False
    session.delete(link)
    return True


def delete_links_for_task(
        session: Session,
        task_id: int,
) -> int:
    """删除某个任务的全部关联。

    删任务前必须调用：task_documents.task_id 是外键且没有 ON DELETE
    CASCADE，否则 Postgres 会因外键约束拒绝删除。
    """
    result = session.execute(
        delete(TaskDocument).where(
            TaskDocument.task_id == task_id,
        )
    )
    return result.rowcount
