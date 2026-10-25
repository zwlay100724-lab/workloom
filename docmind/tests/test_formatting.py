"""P1-13 回归：API/prompt 不能泄漏服务器文件路径。"""
from app.rag.formatting import build_sources, format_context
from langchain_core.documents import Document


def test_sources_and_context_use_basename_only():
    documents = [
        Document(
            page_content="员工年假为 10 天。",
            metadata={
                "source": "/private/data/policy.pdf",
                "page": 0,
                "chunk_index": 0,
                "similarity": 0.9,
            },
        )
    ]

    sources = build_sources(documents)
    assert sources[0]["document"] == "policy.pdf"
    assert sources[0]["page_display"] == "第1页"

    context = format_context(documents)
    assert "policy.pdf" in context
    assert "/private/data" not in context
