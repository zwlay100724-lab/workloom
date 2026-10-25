"""P0-05 回归：不传参数时，切分必须使用 Settings 里的 chunk 配置。"""
from app.core.config import settings
from app.rag.splitters import split_documents
from langchain_core.documents import Document


def test_uses_configured_chunk_size(monkeypatch):
    monkeypatch.setattr(settings, "CHUNK_SIZE", 100)
    monkeypatch.setattr(settings, "CHUNK_OVERLAP", 10)

    chunks = split_documents([Document(page_content="甲" * 500)])

    assert len(chunks) > 1
    assert max(len(chunk.page_content) for chunk in chunks) <= 100


def test_explicit_arguments_override_settings(monkeypatch):
    monkeypatch.setattr(settings, "CHUNK_SIZE", 100)

    chunks = split_documents(
        [Document(page_content="乙" * 500)],
        chunk_size=200,
        chunk_overlap=20,
    )

    assert max(len(chunk.page_content) for chunk in chunks) <= 200
    assert max(len(chunk.page_content) for chunk in chunks) > 100
