"""P1：重建索引递增版本并发出 document.reindexed。"""
from app.models.document import Document
from app.models.outbox_event import OutboxEvent
from app.repositories.vector_repository import VectorRepository
from app.services import document_service as document_service_module
from app.services.document_service import DocumentService
from langchain_core.documents import Document as LangChainDocument
from sqlmodel import Session, select

from .document_lifecycle_helpers import seed_document


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


def test_reindex_increments_version_and_emits_reindexed(
        lifecycle_engine,
        monkeypatch,
):
    doc_id = seed_document(lifecycle_engine, status="completed", version=1)
    _patch_indexing(monkeypatch)

    with Session(lifecycle_engine) as session:
        service = DocumentService(session)
        result = service.process_pdf(doc_id)

        doc = session.get(Document, doc_id)
        assert result["index_version"] == 2
        assert doc.index_version == 2
        event = session.exec(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "document.reindexed"
            )
        ).one()
        assert event.payload["new_index_version"] == 2
