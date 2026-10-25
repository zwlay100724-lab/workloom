"""索引 worker：出队、执行、失败重试、崩溃遗留恢复。

worker 独立于 API 进程运行：
     python -m app.worker
索引包含 PDF 解析和 Embedding 网络调用，放在 API 进程里会占线程；
进程重启也会丢任务，所以用 Redis 队列 + 独立 worker。
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Callable

from sqlmodel import Session, select

from app.core.config import settings
from app.models.document import Document
from app.queue.base import IndexJob, IndexQueue, QueueUnavailableError
from app.services.indexing import index_document

logger = logging.getLogger(__name__)

Indexer = Callable[[int], None]


class IndexWorker:
    def __init__(
            self,
            queue: IndexQueue,
            *,
            bind,
            indexer: Indexer | None = None,
            max_attempts: int | None = None,
            retry_base_delay: float | None = None,
            stale_minutes: int | None = None,
    ) -> None:
        self.queue = queue
        self.bind = bind
        # 默认用真实索引；测试注入 fake。
        self.indexer = indexer or (
            lambda document_id: index_document(
                document_id,
                bind,
                raise_on_error=True,
            )
        )
        self.max_attempts = (
            max_attempts
            if max_attempts is not None
            else settings.INDEX_MAX_ATTEMPTS
        )
        self.retry_base_delay = (
            retry_base_delay
            if retry_base_delay is not None
            else settings.INDEX_RETRY_BASE_DELAY_SECONDS
        )
        self.stale_minutes = (
            stale_minutes
            if stale_minutes is not None
            else settings.INDEX_STALE_MINUTES
        )
        self._last_recovery = 0.0

    def _backoff_seconds(self, attempt: int) -> float:
        """第 attempt 次失败后的等待：base * 2**attempt。"""
        return self.retry_base_delay * (2 ** attempt)

    def _mark_pending(self, document_id: int) -> None:
        """重试间隙把状态从 failed 改回 pending，前端能看出仍在重试。

        这一步失败不应影响重试本身，所以只记日志。
        """
        if self.bind is None:
            return
        try:
            with Session(self.bind) as session:
                document = session.get(Document, document_id)
                if document is not None:
                    document.status = "pending"
                    document.updated_at = datetime.utcnow()
                    session.add(document)
                    session.commit()
        except Exception:
            logger.warning(
                "failed to mark document pending document_id=%s",
                document_id,
                exc_info=True,
            )

    def process(self, job: IndexJob) -> None:
        """执行一条任务；失败时按次数决定重投还是放弃。"""
        try:
            self.indexer(job.document_id)
        except Exception:
            next_job = job.next_attempt()
            if next_job.attempt < self.max_attempts:
                delay = self._backoff_seconds(job.attempt)
                # process_pdf 失败时会把状态写成 failed；重试前改回 pending，
                # 否则轮询的前端会以为已经彻底失败了。
                self._mark_pending(job.document_id)
                self.queue.enqueue(next_job, delay_seconds=delay)
                logger.warning(
                    "index job failed, retrying document_id=%s "
                    "attempt=%s delay=%.1fs",
                    job.document_id,
                    next_job.attempt,
                    delay,
                )
            else:
                # process_pdf 已把文档标记为 failed 并写入 error_message。
                logger.exception(
                    "index job failed permanently document_id=%s attempts=%s",
                    job.document_id,
                    next_job.attempt,
                )
            return

        logger.info(
            "index job completed document_id=%s attempt=%s",
            job.document_id,
            job.attempt,
        )

    def run_once(self, timeout_seconds: int | None = None) -> bool:
        """处理一条任务；返回是否真的取到了任务（测试用）。"""
        self.queue.promote_due()
        job = self.queue.dequeue(
            timeout_seconds=(
                timeout_seconds
                if timeout_seconds is not None
                else settings.INDEX_WORKER_BLOCK_SECONDS
            )
        )
        if job is None:
            return False
        self.process(job)
        return True

    def recover_stale(self) -> int:
        """把长时间停在 processing 的文档重新入队。

        这是 BRPOP 语义下"worker 崩溃丢任务"的兜底：
        队列本身不保存 in-flight 任务，数据库状态是最终事实来源。
        """
        cutoff = datetime.utcnow() - timedelta(
            minutes=self.stale_minutes
        )
        with Session(self.bind) as session:
            # 在 session 内取出需要的字段，避免离开上下文后访问 detached 对象。
            stale_documents = [
                (document.id, document.user_id)
                for document in session.exec(
                    select(Document).where(
                        Document.status == "processing",
                        Document.updated_at < cutoff,
                    )
                ).all()
            ]

        for document_id, user_id in stale_documents:
            self.queue.enqueue(
                IndexJob(
                    document_id=document_id,
                    user_id=user_id,
                )
            )

        if stale_documents:
            logger.warning(
                "re-enqueued stale indexing documents count=%s",
                len(stale_documents),
            )
        return len(stale_documents)

    def run_forever(
            self,
            recovery_interval_seconds: float = 60.0,
    ) -> None:
        logger.info("index worker started")
        while True:
            try:
                now = time.monotonic()
                if now - self._last_recovery >= recovery_interval_seconds:
                    self.recover_stale()
                    self._last_recovery = now
                self.run_once()
            except QueueUnavailableError:
                logger.warning(
                    "index queue unavailable; retrying in 5s",
                    exc_info=True,
                )
                time.sleep(5)
            except Exception:
                logger.exception("index worker loop error")
                time.sleep(1)
