"""重建索引后的来源引用后台迁移。

分工：
- 文档生命周期事件 handler 只登记 RemapJob（和业务更新同事务）；
- 独立 worker 领取任务，用短时 JWT 代表文档所有者调用 DocMind，
  按 source_chunk_uid 批量查询新的 chunk_index；
- 命中 → 更新序号和版本并清失效；未命中 → 标记内容已变化。

为什么不在事件 handler 里直接调 DocMind：
事件消费必须快且可重试，HTTP 超时/下游故障不能阻塞其它文档事件。
"""
import logging
from datetime import datetime, timedelta

from app.clients.docmind import (
    DocMindClient,
    DocMindDocumentNotFoundError,
    DocMindUnavailableError,
)
from app.core.config import settings
from app.core.state import get_shared_redis
from app.models.remap_job import RemapJob
from app.models.task import Task
from app.models.user import User
from app.repositories import task_cache_repository
from redis.exceptions import RedisError
from shared.revocation import get_token_version
from shared.security import create_access_token
from sqlalchemy import func, update
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

REINDEX_REASON = "document_reindexed"
CONTENT_CHANGED_REASON = "document_content_changed"
DELETED_REASON = "document_deleted"

PENDING = "pending"
RUNNING = "running"
DONE = "done"
DEAD = "dead"

# DocMind 的批量解析接口单次上限；配置更大时在这里截断。
MAX_UIDS_PER_RESOLVE = 200


def enqueue_remap_job(
        session: Session,
        *,
        document_id: int,
        user_id: int,
        target_version: int,
) -> None:
    """在事件事务里登记/合并迁移任务。

    同一文档连续重建只保留一行；已经完成或死掉的任务重新激活。
    """
    now = datetime.utcnow()
    job = session.exec(
        select(RemapJob).where(
            RemapJob.document_id == document_id,
            RemapJob.user_id == user_id,
        )
    ).first()

    if job is None:
        session.add(RemapJob(
            document_id=document_id,
            user_id=user_id,
            target_version=target_version,
            status=PENDING,
            next_run_at=now,
            updated_at=now,
        ))
        return

    job.target_version = max(job.target_version, target_version)
    job.updated_at = now
    if job.status in {DONE, DEAD}:
        job.status = PENDING
        job.attempts = 0
        job.next_run_at = now
        job.last_error = None
    session.add(job)


def claim_next_job(bind) -> int | None:
    """原子领取一条到期任务。

    不用 FOR UPDATE：条件更新在 Postgres / SQLite 上都能保证只有一个
    worker 把同一行从 pending 改成 running。
    """
    now = datetime.utcnow()
    with Session(bind) as session:
        job = session.exec(
            select(RemapJob)
            .where(
                RemapJob.status == PENDING,
                RemapJob.next_run_at <= now,
            )
            .order_by(RemapJob.id.asc())
            .limit(1)
        ).first()
        if job is None:
            return None

        result = session.execute(
            update(RemapJob)
            .where(
                RemapJob.id == job.id,
                RemapJob.status == PENDING,
            )
            .values(
                status=RUNNING,
                attempts=job.attempts + 1,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        session.commit()
        if result.rowcount == 0:
            return None
        return job.id


def reset_stale_jobs(bind) -> int:
    """worker 崩溃后，把卡在 running 的任务放回 pending。"""
    cutoff = datetime.utcnow() - timedelta(
        minutes=settings.remap_stale_minutes,
    )
    now = datetime.utcnow()
    with Session(bind) as session:
        result = session.execute(
            update(RemapJob)
            .where(
                RemapJob.status == RUNNING,
                RemapJob.updated_at < cutoff,
            )
            .values(
                status=PENDING,
                next_run_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        session.commit()
        return result.rowcount or 0


def build_service_token(session: Session, user_id: int) -> str | None:
    """给 worker 签发一个代表该用户的短时 JWT。

    DocMind 只验签并读 uid，不查 TaskFlow 的 users 表；ver 取用户当前
    版本，避免用户注销/改密后 token 立刻被共享 Redis 的吊销版本拒绝。
    """
    user = session.get(User, user_id)
    if user is None:
        return None

    version = user.token_version
    try:
        stored = get_token_version(get_shared_redis(), user_id)
        if stored is not None:
            version = max(version, stored)
    except RedisError:
        logger.warning(
            "token version unavailable; using db version user_id=%s",
            user_id,
            exc_info=True,
        )

    return create_access_token(
        subject=user.username,
        user_id=user.id,
        token_version=version,
        secret_key=settings.jwt_secret_key,
        expires_minutes=settings.remap_token_expire_minutes,
    )


def _fetch_batch(
        bind,
        document_id: int,
        user_id: int,
        cursor_id: int,
) -> list[tuple[int, str, int]]:
    """取一批待迁移任务：只处理有 uid、仍处于重建失效状态的。"""
    with Session(bind) as session:
        rows = session.exec(
            select(
                Task.id,
                Task.source_chunk_uid,
                Task.source_index_version,
            )
            .where(
                Task.owner_id == user_id,
                Task.source_document_id == document_id,
                Task.source_invalid_reason == REINDEX_REASON,
                Task.source_chunk_uid.is_not(None),
                Task.id > cursor_id,
            )
            .order_by(Task.id.asc())
            .limit(min(settings.remap_batch_size, MAX_UIDS_PER_RESOLVE))
        ).all()
    return [
        (row[0], row[1], row[2] or 0)
        for row in rows
    ]


def _apply_batch(
        bind,
        items: list[tuple[int, str, int]],
        resolution: dict,
        user_id: int,
) -> None:
    """把一批解析结果写回任务。每条更新都带条件，避免覆盖并发变化。"""
    index_version = int(resolution.get("index_version") or 0)
    by_uid = {
        item.get("chunk_uid"): item
        for item in resolution.get("results", [])
    }
    now = datetime.utcnow()

    with Session(bind) as session:
        for task_id, chunk_uid, old_version in items:
            item = by_uid.get(chunk_uid) or {}
            resolved = (
                item.get("resolved")
                and item.get("chunk_index") is not None
            )

            if resolved and index_version > old_version:
                session.execute(
                    update(Task)
                    .where(
                        Task.id == task_id,
                        Task.source_invalid_reason == REINDEX_REASON,
                        Task.source_chunk_uid == chunk_uid,
                        func.coalesce(Task.source_index_version, 0)
                        < index_version,
                    )
                    .values(
                        source_chunk_index=item["chunk_index"],
                        source_index_version=index_version,
                        source_invalidated_at=None,
                        source_invalid_reason=None,
                    )
                    .execution_options(synchronize_session=False)
                )
            elif resolved and index_version == old_version:
                # 版本已经追平，只需清掉残留的失效标记。
                session.execute(
                    update(Task)
                    .where(
                        Task.id == task_id,
                        Task.source_invalid_reason == REINDEX_REASON,
                        Task.source_chunk_uid == chunk_uid,
                        Task.source_index_version == index_version,
                    )
                    .values(
                        source_invalidated_at=None,
                        source_invalid_reason=None,
                    )
                    .execution_options(synchronize_session=False)
                )
            else:
                session.execute(
                    update(Task)
                    .where(
                        Task.id == task_id,
                        Task.source_invalid_reason == REINDEX_REASON,
                        Task.source_chunk_uid == chunk_uid,
                    )
                    .values(
                        source_invalidated_at=now,
                        source_invalid_reason=CONTENT_CHANGED_REASON,
                    )
                    .execution_options(synchronize_session=False)
                )
        session.commit()

    task_cache_repository.invalidate_task_list_cache(user_id)


def _mark_document_deleted(
        bind,
        document_id: int,
        user_id: int,
) -> None:
    now = datetime.utcnow()
    with Session(bind) as session:
        session.execute(
            update(Task)
            .where(
                Task.owner_id == user_id,
                Task.source_document_id == document_id,
                Task.source_invalid_reason == REINDEX_REASON,
            )
            .values(
                source_invalidated_at=now,
                source_invalid_reason=DELETED_REASON,
            )
            .execution_options(synchronize_session=False)
        )
        session.commit()
    task_cache_repository.invalidate_task_list_cache(user_id)


def _count_remaining(
        bind,
        document_id: int,
        user_id: int,
) -> int:
    with Session(bind) as session:
        return session.exec(
            select(func.count())
            .select_from(Task)
            .where(
                Task.owner_id == user_id,
                Task.source_document_id == document_id,
                Task.source_invalid_reason == REINDEX_REASON,
                Task.source_chunk_uid.is_not(None),
            )
        ).one()


def _finish_job(
        bind,
        job_id: int,
        *,
        status: str,
        error: str | None = None,
) -> None:
    with Session(bind) as session:
        job = session.get(RemapJob, job_id)
        if job is None:
            return
        job.status = status
        job.last_error = error[:1000] if error else None
        job.updated_at = datetime.utcnow()
        session.add(job)
        session.commit()


def _requeue_for_newer_version(bind, job_id: int) -> None:
    """处理期间又发生了重建：保留任务，按最新版本再跑一轮。"""
    now = datetime.utcnow()
    with Session(bind) as session:
        job = session.get(RemapJob, job_id)
        if job is None:
            return
        job.status = PENDING
        job.attempts = 0
        job.next_run_at = now
        job.last_error = "newer index version arrived"
        job.updated_at = now
        session.add(job)
        session.commit()


def retry_job(bind, job_id: int, error: Exception) -> None:
    """失败退避；超过上限进入 dead，避免无意义地持续调用 DocMind。"""
    now = datetime.utcnow()
    with Session(bind) as session:
        job = session.get(RemapJob, job_id)
        if job is None:
            return

        job.last_error = str(error)[:1000]
        job.updated_at = now
        if job.attempts >= settings.remap_max_attempts:
            job.status = DEAD
        else:
            job.status = PENDING
            delay = settings.remap_retry_base_delay * (
                2 ** max(0, job.attempts - 1)
            )
            job.next_run_at = now + timedelta(seconds=delay)
        session.add(job)
        session.commit()


async def process_job(
        bind,
        job_id: int,
        docmind: DocMindClient,
) -> None:
    """执行一条迁移任务：分批查询任务、批量解析、写回结果。"""
    with Session(bind) as session:
        job = session.get(RemapJob, job_id)
        if job is None or job.status != RUNNING:
            return

        document_id = job.document_id
        user_id = job.user_id
        target_version = job.target_version

    cursor_id = 0
    while True:
        items = _fetch_batch(bind, document_id, user_id, cursor_id)
        if not items:
            break
        cursor_id = items[-1][0]

        # 每批重新签发短时 token：大批量迁移可能超过单次 token 有效期。
        with Session(bind) as session:
            token = build_service_token(session, user_id)
        if token is None:
            _finish_job(bind, job_id, status=DONE, error="user not found")
            return

        chunk_uids = sorted({chunk_uid for _, chunk_uid, _ in items})
        try:
            resolution = await docmind.resolve_chunk_uids(
                document_id=document_id,
                chunk_uids=chunk_uids,
                token=token,
            )
        except DocMindDocumentNotFoundError:
            # 文档在迁移前被删除：读路径已经不负责修复，这里兜底。
            _mark_document_deleted(bind, document_id, user_id)
            _finish_job(
                bind,
                job_id,
                status=DONE,
                error="document not found",
            )
            return
        except DocMindUnavailableError as exc:
            retry_job(bind, job_id, exc)
            return

        _apply_batch(bind, items, resolution, user_id)

    with Session(bind) as session:
        job = session.get(RemapJob, job_id)
        if job is None:
            return
        target_changed = job.target_version > target_version

    if target_changed:
        _requeue_for_newer_version(bind, job_id)
        return

    remaining = _count_remaining(bind, document_id, user_id)
    if remaining > 0:
        retry_job(bind, job_id, RuntimeError(
            f"{remaining} citations still stale"
        ))
        return

    _finish_job(bind, job_id, status=DONE)
