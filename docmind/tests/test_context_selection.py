"""最终上下文裁剪：总量、单文档配额、近似去重。"""
from app.rag.context_selection import select_documents
from langchain_core.documents import Document


def _doc(
        content: str,
        document_id: int,
        chunk_index: int,
        score: float,
) -> Document:
    return Document(
        page_content=content,
        metadata={
            "document_id": document_id,
            "chunk_index": chunk_index,
            "retrieval_score": score,
        },
    )


def test_selects_highest_scores_and_caps_total():
    documents = [
        _doc(f"content-{index}", 1, index, index / 10)
        for index in range(5)
    ]

    selected = select_documents(
        documents,
        max_total=2,
        max_per_document=5,
        duplicate_threshold=0,
    )

    assert [doc.metadata["chunk_index"] for doc in selected] == [4, 3]


def test_caps_per_document():
    documents = [
        _doc("doc1-a", 1, 0, 0.9),
        _doc("doc1-b", 1, 1, 0.8),
        _doc("doc2-a", 2, 0, 0.7),
        _doc("doc2-b", 2, 1, 0.6),
    ]

    selected = select_documents(
        documents,
        max_total=4,
        max_per_document=1,
        duplicate_threshold=0,
    )

    assert [doc.metadata["document_id"] for doc in selected] == [1, 2]


def test_drops_near_duplicate_content():
    documents = [
        _doc("完全相同的一段上下文内容", 1, 0, 0.9),
        _doc("完全相同的一段上下文内容", 2, 0, 0.8),
        _doc("完全不同的另一段上下文", 3, 0, 0.7),
    ]

    selected = select_documents(
        documents,
        max_total=3,
        max_per_document=3,
        duplicate_threshold=0.85,
    )

    assert len(selected) == 2
    assert selected[0].metadata["document_id"] == 1
    assert selected[1].metadata["document_id"] == 3


def test_missing_scores_are_stable():
    documents = [
        Document(page_content="a", metadata={"document_id": 1, "chunk_index": 0}),
        Document(page_content="b", metadata={"document_id": 1, "chunk_index": 1}),
    ]

    selected = select_documents(
        documents,
        max_total=1,
        max_per_document=1,
        duplicate_threshold=0,
    )

    assert len(selected) == 1
