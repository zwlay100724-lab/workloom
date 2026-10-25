"""DocMindClient 的 HTTP 契约测试。

用 httpx.MockTransport 校验真实请求的路径、鉴权头、请求体，
以及 404 / 5xx / 网络错误到业务异常的映射。不依赖运行中的 DocMind。
"""
import asyncio
import json

import httpx
import pytest
from app.clients.docmind import (
    DocMindClient,
    DocMindDocumentNotFoundError,
    DocMindUnavailableError,
)


def _client(handler) -> DocMindClient:
    return DocMindClient(
        base_url="http://docmind.test",
        transport=httpx.MockTransport(handler),
    )


def test_search_sends_bearer_token_and_payload():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["authorization"] = request.headers["authorization"]
        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"results": [{"document_id": 7}]},
        )

    results = asyncio.run(
        _client(handler).search(
            query="年假",
            token="user-token",
            top_k=3,
            document_id=7,
        )
    )

    assert results == [{"document_id": 7}]
    assert captured["path"] == "/retrieval/search"
    assert captured["authorization"] == "Bearer user-token"
    assert captured["json"] == {
        "query": "年假",
        "top_k": 3,
        "mode": "vector",
        "document_id": 7,
    }


def test_search_non_2xx_raises_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with pytest.raises(DocMindUnavailableError):
        asyncio.run(
            _client(handler).search(query="年假", token="t")
        )


def test_get_document_returns_metadata():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["authorization"] = request.headers["authorization"]
        return httpx.Response(
            200,
            json={"id": 7, "filename": "plan.pdf", "status": "completed"},
        )

    document = asyncio.run(
        _client(handler).get_document(
            document_id=7,
            token="user-token",
        )
    )

    assert document["filename"] == "plan.pdf"
    assert captured["path"] == "/documents/7"
    assert captured["authorization"] == "Bearer user-token"


def test_get_document_404_raises_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(DocMindDocumentNotFoundError):
        asyncio.run(
            _client(handler).get_document(document_id=99, token="t")
        )


def test_network_error_raises_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(DocMindUnavailableError):
        asyncio.run(
            _client(handler).get_document(document_id=7, token="t")
        )
