"""AI 助手的 search_knowledge 工具测试。

只验证工具层：注册条件、参数转发、成功 / 空结果 / 下游故障的返回形状。
不涉及 LLM 对话循环。
"""
import asyncio

from app.clients.docmind import DocMindUnavailableError
from app.llm.tools import build_task_tools


class FakeDocMindClient:
    def __init__(
            self,
            results: list[dict] | None = None,
            error: Exception | None = None,
    ) -> None:
        self.results = results or []
        self.error = error
        self.calls: list[dict] = []

    async def search(
            self,
            *,
            query: str,
            token: str,
            top_k: int = 5,
            document_id: int | None = None,
            mode: str = "vector",
    ) -> list[dict]:
        self.calls.append({
            "query": query,
            "token": token,
            "top_k": top_k,
        })
        if self.error is not None:
            raise self.error
        return self.results


def _tool_map(**kwargs) -> dict:
    tools = build_task_tools(
        session=None,
        owner_id=1,
        **kwargs,
    )
    return {tool.name: tool for tool in tools}


def test_tool_is_only_registered_with_token():
    assert "search_knowledge" not in _tool_map()

    tools = _tool_map(
        access_token="token",
        docmind_client=FakeDocMindClient(),
    )
    assert "search_knowledge" in tools


def test_search_knowledge_forwards_arguments():
    client = FakeDocMindClient(results=[{
        "document_id": 1,
        "filename": "handbook.pdf",
        "chunk_index": 0,
        "page": 1,
        "content": "员工每年享有 10 天带薪年假。",
        "score": 0.9,
    }])
    tools = _tool_map(
        access_token="user-token",
        docmind_client=client,
    )

    result = asyncio.run(
        tools["search_knowledge"].ainvoke({
            "query": "年假有几天",
            "top_k": 2,
        })
    )

    assert result["success"] is True
    assert result["results"][0]["filename"] == "handbook.pdf"
    assert client.calls == [{
        "query": "年假有几天",
        "token": "user-token",
        "top_k": 2,
    }]


def test_empty_results_returns_message():
    tools = _tool_map(
        access_token="token",
        docmind_client=FakeDocMindClient(results=[]),
    )

    result = asyncio.run(
        tools["search_knowledge"].ainvoke({"query": "不存在的内容"})
    )

    assert result == {
        "success": True,
        "results": [],
        "message": "知识库中没有找到相关内容。",
    }


def test_docmind_failure_is_returned_as_error():
    tools = _tool_map(
        access_token="token",
        docmind_client=FakeDocMindClient(
            error=DocMindUnavailableError("down")
        ),
    )

    result = asyncio.run(
        tools["search_knowledge"].ainvoke({"query": "年假"})
    )

    assert result["success"] is False
    assert "down" in result["error"]
