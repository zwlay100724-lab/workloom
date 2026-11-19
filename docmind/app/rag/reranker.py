"""检索结果精排。

默认关闭（RERANK_ENABLED=false），避免没有 rerank 服务时拖慢请求。
开启后调用 DashScope 的 text-rerank 原生接口（gte-rerank-v2 等），
也可以注入 httpx.Client 用于测试或替换成别的 rerank 服务。

和 RRF 的分工：
- RRF 负责把向量 / 全文 / 关键词三路召回融合起来，偏召回；
- Reranker 负责对融合后的候选做 query-document 精排，偏精度。
"""
import logging
from functools import lru_cache
from typing import Any

import httpx
from langchain_core.documents import Document

from app.core.config import settings
from app.rag.context_selection import document_score

logger = logging.getLogger(__name__)


class BaseReranker:
    is_active = False

    def rerank(
            self,
            query: str,
            documents: list[Document],
            *,
            top_k: int,
            score_threshold: float = 0.0,
    ) -> list[Document]:
        raise NotImplementedError


class NoopReranker(BaseReranker):
    """不调用外部服务：按已有检索分数排序后截断。"""

    is_active = False

    def rerank(
            self,
            query: str,
            documents: list[Document],
            *,
            top_k: int,
            score_threshold: float = 0.0,
    ) -> list[Document]:
        ordered = sorted(
            documents,
            key=document_score,
            reverse=True,
        )
        return ordered[:top_k]


class DashScopeReranker(BaseReranker):
    """调用 DashScope text-rerank 接口。

    响应兼容：
      {"output": {"results": [{"index": 0, "relevance_score": 0.9}]}}
    以及部分代理返回的顶层 results。
    """

    is_active = True

    def __init__(
            self,
            *,
            base_url: str | None = None,
            model: str | None = None,
            api_key: str | None = None,
            timeout: float | None = None,
            client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url or settings.RERANK_BASE_URL
        self.model = model or settings.RERANK_MODEL
        self.api_key = (
            api_key
            or settings.RERANK_API_KEY
            or settings.EMBED_API_KEY
            or settings.LLM_API_KEY
        )
        self.timeout = (
            timeout
            if timeout is not None
            else settings.RERANK_TIMEOUT_SECONDS
        )
        self._client = client or httpx.Client(timeout=self.timeout)
        self._available = True

    def _fallback(
            self,
            query: str,
            documents: list[Document],
            *,
            top_k: int,
            error: str,
    ) -> list[Document]:
        if self._available:
            logger.warning(
                "rerank failed, falling back to retrieval score: %s",
                error,
            )
        self._available = False
        return NoopReranker().rerank(
            query,
            documents,
            top_k=top_k,
        )

    def rerank(
            self,
            query: str,
            documents: list[Document],
            *,
            top_k: int,
            score_threshold: float = 0.0,
    ) -> list[Document]:
        if not documents:
            return []
        if len(documents) == 1:
            return documents[:top_k]
        if not self._available:
            return self._fallback(
                query,
                documents,
                top_k=top_k,
                error="reranker unavailable",
            )

        payload: dict[str, Any] = {
            "model": self.model,
            "input": {
                "query": query,
                "documents": [
                    document.page_content
                    for document in documents
                ],
            },
            "parameters": {
                "return_documents": False,
                "top_n": len(documents),
            },
        }

        try:
            response = self._client.post(
                self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return self._fallback(
                query,
                documents,
                top_k=top_k,
                error=f"{type(exc).__name__}: {exc}",
            )

        results = (
            (data.get("output") or {}).get("results")
            or data.get("results")
            or []
        )
        scored: list[tuple[float, int]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            try:
                index = int(item["index"])
                raw_score = item.get("relevance_score")
                if raw_score is None:
                    raw_score = item.get("score")
                score = float(raw_score or 0.0)
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= index < len(documents):
                scored.append((score, index))

        if not scored:
            return self._fallback(
                query,
                documents,
                top_k=top_k,
                error="rerank returned no usable results",
            )

        scored.sort(key=lambda pair: pair[0], reverse=True)
        selected: list[Document] = []
        for score, index in scored:
            if score_threshold > 0 and score < score_threshold:
                continue
            document = documents[index]
            document.metadata["rerank_score"] = score
            document.metadata["retrieval_score"] = score
            selected.append(document)
            if len(selected) >= top_k:
                break
        return selected


@lru_cache(maxsize=1)
def build_reranker() -> BaseReranker:
    """根据配置创建 reranker；未启用或没有 key 时降级为 Noop。

    进程内复用同一个实例（以及同一个 httpx.Client），
    避免每次工具调用都新建连接池。
    """
    if not settings.RERANK_ENABLED:
        return NoopReranker()

    api_key = (
        settings.RERANK_API_KEY
        or settings.EMBED_API_KEY
        or settings.LLM_API_KEY
    )
    if not api_key:
        logger.warning(
            "RERANK_ENABLED=true 但没有可用的 API key，跳过 rerank"
        )
        return NoopReranker()

    return DashScopeReranker(api_key=api_key)
