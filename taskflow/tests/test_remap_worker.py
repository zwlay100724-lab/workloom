"""后台引用迁移 worker 的回归测试。"""
import asyncio
from datetime import datetime

import pytest
from app.clients.docmind import (
    DocMindDocumentNotFoundError,
    DocMindUnavailableError,
)
from app.core.config import settings
from app.models.remap_job import RemapJob
from app.models.task import Task
from app.models.user import User
from app.repositories import task_cache_repository
from app.services import remap_service
from shared.security import decode_access_token_claims
from sqlmodel import Session, select

DOC_ID = 10
UID_A = "a" * 64
UID_B = "b" * 64


class FakeDocMind:
    def __init__(self, response: dict | None = None, error=None) -> None:
        self.response = response or {
            "document_id": DOC_ID,
            "index_version": 3,
            "results": [{
                "chunk_uid": UID_A,
                "resolved": True,
                "chunk_index": 7,
                "page": 2,
            }],
        }
        self.error = error
        self.calls: list[dict] = []

    async def resolve_chunk_uids(
        self,
        *,
        document_id: int,
        chunk_uids: list[str],
        token: str,
    ) -> dict:
        self.calls.append({
            "document_id": document_id,
            "chunk_uids": chunk_uids,
            "token": token,
        })
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture(autouse=True)
def _stub_side_effects(monkeypatch):
    monkeypatch.setattr(
        task_cache_repository,
        "invalidate_task_list_cache",
        lambda user_id: None,
    )
    monkeypatch.setattr(remap_service, "get_shared_redis", lambda: object())
    monkeypatch.setattr(
        remap_service,
        "get_token_version",
        lambda redis, user_id: None,
    )


def _seed(
        engine,
        *,
        chunk_uid: str | None = UID_A,
        reason: str = "document_reindexed",
        version: int = 1,
        token_version: int = 3,
        job_status: str = "running",
):
    with Session(engine) as session:
        user = User(
            username="alice",
            hashed_password="hashed",
            token_version=token_version,
        )
        session.add(user)
        session.commit()
        session.refresh(user)

        task = Task(
            title="重建索引后需要修复的引用",
            owner_id=user.id,
            source_document_id=DOC_ID,
            source_filename="plan.pdf",
            source_chunk_index=0,
            source_chunk_uid=chunk_uid,
            source_index_version=version,
            source_invalidated_at=datetime.utcnow(),
            source_invalid_reason=reason,
        )
        session.add(task)

        job = RemapJob(
            document_id=DOC_ID,
            user_id=user.id,
            target_version=2,
            status=job_status,
            attempts=1,
            next_run_at=datetime.utcnow(),
        )
        session.add(job)
        session.commit()
        session.refresh(task)
        session.refresh(job)
        return user.id, task.id, job.id


def test_enqueue_coalesces_document_jobs(test_engine):
    with Session(test_engine) as session:
        remap_service.enqueue_remap_job(
            session,
            document_id=DOC_ID,
            user_id=1,
            target_version=2,
        )
        remap_service.enqueue_remap_job(
            session,
            document_id=DOC_ID,
            user_id=1,
            target_version=3,
        )
        session.commit()

        jobs = session.exec(select(RemapJob)).all()

    assert len(jobs) == 1
    assert jobs[0].target_version == 3
    assert jobs[0].status == "pending"


def test_process_job_updates_resolved_citation(test_engine):
    user_id, task_id, job_id = _seed(test_engine)
    fake = FakeDocMind()

    asyncio.run(remap_service.process_job(
        test_engine,
        job_id,
        fake,
    ))

    with Session(test_engine) as session:
        task = session.get(Task, task_id)
        job = session.get(RemapJob, job_id)

    assert task.source_chunk_index == 7
    assert task.source_index_version == 3
    assert task.source_invalidated_at is None
    assert task.source_invalid_reason is None
    assert job.status == "done"

    call = fake.calls[0]
    assert call["document_id"] == DOC_ID
    assert call["chunk_uids"] == [UID_A]
    claims = decode_access_token_claims(
        call["token"],
        secret_key=settings.jwt_secret_key,
    )
    assert claims is not None
    assert claims.user_id == user_id
    assert claims.token_version == 3


def test_token_uses_max_of_db_and_shared_redis(test_engine, monkeypatch):
    _, _, job_id = _seed(test_engine, token_version=3)
    monkeypatch.setattr(
        remap_service,
        "get_token_version",
        lambda redis, user_id: 8,
    )
    fake = FakeDocMind()

    asyncio.run(remap_service.process_job(
        test_engine,
        job_id,
        fake,
    ))

    claims = decode_access_token_claims(
        fake.calls[0]["token"],
        secret_key=settings.jwt_secret_key,
    )
    assert claims is not None
    assert claims.token_version == 8


def test_unresolved_chunk_marks_content_changed(test_engine):
    _, task_id, job_id = _seed(test_engine)
    fake = FakeDocMind({
        "document_id": DOC_ID,
        "index_version": 3,
        "results": [{
            "chunk_uid": UID_A,
            "resolved": False,
            "chunk_index": None,
            "page": None,
        }],
    })

    asyncio.run(remap_service.process_job(
        test_engine,
        job_id,
        fake,
    ))

    with Session(test_engine) as session:
        task = session.get(Task, task_id)
        job = session.get(RemapJob, job_id)

    assert task.source_chunk_index == 0
    assert task.source_index_version == 1
    assert task.source_invalid_reason == "document_content_changed"
    assert job.status == "done"


def test_task_without_uid_is_ignored(test_engine):
    _, task_id, job_id = _seed(test_engine, chunk_uid=None)
    fake = FakeDocMind()

    asyncio.run(remap_service.process_job(
        test_engine,
        job_id,
        fake,
    ))

    with Session(test_engine) as session:
        task = session.get(Task, task_id)
        job = session.get(RemapJob, job_id)

    # 存量任务没有 uid：留给用户重新生成，不调用 DocMind。
    assert task.source_invalid_reason == "document_reindexed"
    assert fake.calls == []
    assert job.status == "done"


def test_docmind_unavailable_requeues_with_backoff(test_engine):
    _, task_id, job_id = _seed(test_engine)
    fake = FakeDocMind(error=DocMindUnavailableError("down"))

    asyncio.run(remap_service.process_job(
        test_engine,
        job_id,
        fake,
    ))

    with Session(test_engine) as session:
        task = session.get(Task, task_id)
        job = session.get(RemapJob, job_id)

    assert task.source_invalid_reason == "document_reindexed"
    assert job.status == "pending"
    assert job.attempts == 1
    assert job.next_run_at > datetime.utcnow()
    assert "down" in job.last_error


def test_document_missing_marks_citations_deleted(test_engine):
    _, task_id, job_id = _seed(test_engine)
    fake = FakeDocMind(
        error=DocMindDocumentNotFoundError("gone"),
    )

    asyncio.run(remap_service.process_job(
        test_engine,
        job_id,
        fake,
    ))

    with Session(test_engine) as session:
        task = session.get(Task, task_id)
        job = session.get(RemapJob, job_id)

    assert task.source_invalid_reason == "document_deleted"
    assert job.status == "done"


def test_newer_reindex_requeues_job(test_engine):
    _, _, job_id = _seed(test_engine)

    class BumpVersionFake(FakeDocMind):
        async def resolve_chunk_uids(self, **kwargs):
            result = await super().resolve_chunk_uids(**kwargs)
            with Session(test_engine) as session:
                job = session.get(RemapJob, job_id)
                job.target_version = 3
                session.add(job)
                session.commit()
            return result

    asyncio.run(remap_service.process_job(
        test_engine,
        job_id,
        BumpVersionFake(),
    ))

    with Session(test_engine) as session:
        job = session.get(RemapJob, job_id)

    # 处理期间又来了 v3 重建：不能直接 done，要按最新版本再跑一轮。
    assert job.status == "pending"
    assert job.target_version == 3


def test_claim_next_job_marks_running(test_engine):
    _, _, job_id = _seed(test_engine, job_status="pending")

    claimed = remap_service.claim_next_job(test_engine)
    second = remap_service.claim_next_job(test_engine)

    with Session(test_engine) as session:
        job = session.get(RemapJob, job_id)

    assert claimed == job_id
    assert second is None
    assert job.status == "running"
    assert job.attempts == 2  # seed 是 1，领取时 +1
