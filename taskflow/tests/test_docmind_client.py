"""DocMindClient 的 HTTP 契约测试。

用 httpx.MockTransport 校验真实请求的路径、鉴权头、请求体，
以及 404 / 5xx / 网络错误到业务异常的映射。不依赖运行中的 DocMind。
"""
import asyncio
import json

import httpx
import pytest
from app.clients.docmind import (
    DocMindChunkNotFoundError,
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


def test_search_404_raises_document_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(DocMindDocumentNotFoundError):
        asyncio.run(
            _client(handler).search(
                query="年假",
                token="t",
                document_id=7,
            )
        )


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


def test_get_chunk_sends_path_and_token():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["authorization"] = request.headers["authorization"]
        return httpx.Response(
            200,
            json={
                "document_id": 7,
                "chunk_index": 3,
                "chunk_uid": "a" * 64,
                "page": 2,
                "content": "原文",
            },
        )

    chunk = asyncio.run(
        _client(handler).get_chunk(
            document_id=7,
            chunk_index=3,
            token="user-token",
        )
    )

    assert chunk["chunk_uid"] == "a" * 64
    assert captured["path"] == "/documents/7/chunks/3"
    assert captured["authorization"] == "Bearer user-token"


def test_get_chunk_404_raises_chunk_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(DocMindChunkNotFoundError):
        asyncio.run(
            _client(handler).get_chunk(
                document_id=7,
                chunk_index=99,
                token="t",
            )
        )


def test_resolve_chunk_uid_returns_resolution():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "document_id": 7,
                "index_version": 2,
                "resolved": True,
                "chunk_uid": "a" * 64,
                "chunk_index": 5,
            },
        )

    result = asyncio.run(
        _client(handler).resolve_chunk_uid(
            document_id=7,
            chunk_uid="a" * 64,
            token="t",
        )
    )

    assert result["resolved"] is True
    assert result["chunk_index"] == 5
    assert captured["path"] == f"/documents/7/chunks/by-uid/{'a' * 64}"


def test_resolve_chunk_uid_404_raises_document_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(DocMindDocumentNotFoundError):
        asyncio.run(
            _client(handler).resolve_chunk_uid(
                document_id=7,
                chunk_uid="a" * 64,
                token="t",
            )
        )


def test_resolve_chunk_uids_posts_batch_payload():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["authorization"] = request.headers["authorization"]
        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "document_id": 7,
                "index_version": 3,
                "results": [{
                    "chunk_uid": "a" * 64,
                    "resolved": True,
                    "chunk_index": 5,
                    "page": 2,
                }],
            },
        )

    result = asyncio.run(
        _client(handler).resolve_chunk_uids(
            document_id=7,
            chunk_uids=["a" * 64, "b" * 64],
            token="user-token",
        )
    )

    assert result["index_version"] == 3
    assert captured["path"] == "/documents/7/chunks/resolve-by-uid"
    assert captured["authorization"] == "Bearer user-token"
    assert captured["json"] == {
        "chunk_uids": ["a" * 64, "b" * 64],
    }
