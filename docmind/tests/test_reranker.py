"""Reranker：API 精排、阈值和失败降级。"""
import httpx
from app.rag.reranker import DashScopeReranker, NoopReranker
from langchain_core.documents import Document


def _doc(content: str, score: float) -> Document:
    return Document(
        page_content=content,
        metadata={"retrieval_score": score},
    )


def test_noop_sorts_by_retrieval_score():
    documents = [
        _doc("low", 0.1),
        _doc("high", 0.9),
        _doc("mid", 0.5),
    ]

    selected = NoopReranker().rerank(
        "q",
        documents,
        top_k=2,
    )

    assert [doc.page_content for doc in selected] == ["high", "mid"]


def test_dashscope_rerank_reorders_and_applies_threshold():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": {
                    "results": [
                        {"index": 2, "relevance_score": 0.1},
                        {"index": 0, "relevance_score": 0.9},
                        {"index": 1, "relevance_score": 0.5},
                    ],
                },
            },
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
    )
    reranker = DashScopeReranker(
        base_url="https://rerank.test/rerank",
        model="gte-rerank-v2",
        api_key="test-key",
        client=client,
    )
    documents = [
        _doc("first", 0.2),
        _doc("second", 0.3),
        _doc("third", 0.4),
    ]

    selected = reranker.rerank(
        "q",
        documents,
        top_k=2,
        score_threshold=0.4,
    )

    assert [doc.page_content for doc in selected] == ["first", "second"]
    assert selected[0].metadata["rerank_score"] == 0.9


def test_dashscope_failure_falls_back_to_retrieval_score():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(500, text="upstream down")

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
    )
    reranker = DashScopeReranker(
        base_url="https://rerank.test/rerank",
        model="gte-rerank-v2",
        api_key="test-key",
        client=client,
    )
    documents = [
        _doc("low", 0.1),
        _doc("high", 0.9),
    ]

    first = reranker.rerank("q", documents, top_k=1)
    second = reranker.rerank("q", documents, top_k=1)

    assert [doc.page_content for doc in first] == ["high"]
    assert [doc.page_content for doc in second] == ["high"]
    # 失败后缓存不可用状态，不继续打 rerank 服务。
    assert calls["count"] == 1
