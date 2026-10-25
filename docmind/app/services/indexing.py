"""后台索引任务。

FastAPI 的 BackgroundTasks 在响应发出后执行；这里必须自己创建 Session，
不能复用请求级的 session（它已经随请求关闭）。
"""
import logging
from typing import Any

from sqlmodel import Session

from app.services.document_service import DocumentService

logger = logging.getLogger(__name__)


def index_document(
        document_id: int,
        bind: Any,
        *,
        raise_on_error: bool = False,
) -> None:
    """在后台线程里索引文档。

    bind 是请求级 Session 的 engine/connection（session.get_bind()），
    这样测试里 dependency override 到独立测试库时，后台任务也会写同一个库。

    raise_on_error：队列 worker 需要感知失败以便重试；而 FastAPI 的
    BackgroundTasks 必须吞掉异常，否则会冒泡到 Starlette。
    """
    with Session(bind) as session:
        try:
            DocumentService(session).process_pdf(document_id)
        except Exception:
            # process_pdf 已经把文档状态置为 failed；这里负责记录日志。
            logger.exception(
                "background indexing failed document_id=%s",
                document_id,
            )
            session.rollback()
            if raise_on_error:
                raise
