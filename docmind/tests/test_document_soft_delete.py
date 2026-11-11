"""P0：文档软删除、公开读 404、幂等。"""
import pytest
from app.core.exceptions import DocumentNotFoundError
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.outbox_event import OutboxEvent
from app.services.document_service import DocumentService
from sqlmodel import Session, select

from .document_lifecycle_helpers import USER_ID, seed_document


def test_soft_delete_keeps_document_but_removes_chunks(lifecycle_engine):
    doc_id = seed_document(lifecycle_engine)

    with Session(lifecycle_engine) as session:
        service = DocumentService(session)
        service.delete_document(doc_id, USER_ID)

        doc = session.get(Document, doc_id)
        assert doc.deleted_at is not None
        assert doc.status == "deleted"
        assert session.exec(select(DocumentChunk)).all() == []

        event = session.exec(select(OutboxEvent)).one()
        assert event.event_type == "document.deleted"
        assert event.aggregate_id == doc_id


def test_delete_is_idempotent_and_public_reads_404(lifecycle_engine):
    doc_id = seed_document(lifecycle_engine)

    with Session(lifecycle_engine) as session:
        service = DocumentService(session)
        service.delete_document(doc_id, USER_ID)
        service.delete_document(doc_id, USER_ID)

        assert len(session.exec(select(OutboxEvent)).all()) == 1
        with pytest.raises(DocumentNotFoundError):
            service.get_owned_document(doc_id, USER_ID)

        items, total = service.list_documents(USER_ID)
        assert items == []
        assert total == 0
