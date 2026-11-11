"""P2：重命名快照、恢复后重建索引、restored 事件。"""
from app.models.document import Document
from app.models.outbox_event import OutboxEvent
from app.repositories.vector_repository import VectorRepository
from app.services import document_service as document_service_module
from app.services.document_service import DocumentService
from langchain_core.documents import Document as LangChainDocument
from sqlmodel import Session, select

from .document_lifecycle_helpers import USER_ID, seed_document


def _patch_indexing(monkeypatch):
    monkeypatch.setattr(
        document_service_module,
        "load_pdf",
        lambda path: [LangChainDocument(page_content="x", metadata={"page": 0})],
    )
    monkeypatch.setattr(
        document_service_module,
        "split_documents",
        lambda docs: [LangChainDocument(
            page_content="x",
            metadata={"chunk_index": 0, "page": 0, "source": "a.pdf"},
        )],
    )
    monkeypatch.setattr(
        document_service_module,
        "get_embeddings",
        lambda: type("E", (), {
            "embed_documents": lambda self, texts: [[0.1] * 4 for _ in texts],
        })(),
    )
    monkeypatch.setattr(
        VectorRepository,
        "add_chunks",
        lambda self, **kwargs: None,
    )


def test_restore_marks_pending_and_is_visible_again(lifecycle_engine):
    doc_id = seed_document(lifecycle_engine)

    with Session(lifecycle_engine) as session:
        service = DocumentService(session)
        service.delete_document(doc_id, USER_ID)
        restored = service.restore_document(doc_id, USER_ID)

        assert restored.deleted_at is None
        assert restored.status == "pending"
        assert restored.restore_requested is True

        items, total = service.list_documents(USER_ID)
        assert total == 1
        assert items[0].id == doc_id


def test_rename_writes_event_and_updates_filename(lifecycle_engine):
    doc_id = seed_document(lifecycle_engine)

    with Session(lifecycle_engine) as session:
        service = DocumentService(session)
        doc = service.rename_document(doc_id, USER_ID, "会议纪要-v2.pdf")

        assert doc.filename == "会议纪要-v2.pdf"
        event = session.exec(select(OutboxEvent)).one()
        assert event.event_type == "document.renamed"
        assert event.payload["filename"] == "会议纪要-v2.pdf"


def test_restore_reindex_emits_restored_not_reindexed(
        lifecycle_engine,
        monkeypatch,
):
    doc_id = seed_document(lifecycle_engine, status="completed", version=1)
    _patch_indexing(monkeypatch)

    with Session(lifecycle_engine) as session:
        service = DocumentService(session)
        service.delete_document(doc_id, USER_ID)
        service.restore_document(doc_id, USER_ID)
        service.process_pdf(doc_id)

        doc = session.get(Document, doc_id)
        assert doc.index_version == 2
        assert doc.restore_requested is False
        events = session.exec(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "document.restored"
            )
        ).all()
        assert len(events) == 1
