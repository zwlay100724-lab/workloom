"""TaskFlow → DocMind 的 HTTP 客户端。

两个服务数据库独立（不共享表、不建跨库外键），跨服务能力只能走 HTTP。
身份继续沿用既有的 JWT 契约：把当前用户的 access token 原样透传，
DocMind 只验签、不查 TaskFlow 的 users 表。

为什么单独抽一个类，而不是在工具 / 路由里直接写 httpx：
- 超时、错误语义集中在一处，HTTP 细节不进业务代码；
- 测试可以注入 fake client，不必真的起一个 DocMind。
"""
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class DocMindUnavailableError(Exception):
    """DocMind 不可达、超时，或返回了非 2xx 响应。"""


class DocMindDocumentNotFoundError(Exception):
    """文档不存在，或不属于当前用户（DocMind 统一返回 404）。"""


class DocMindClient:
    def __init__(
            self,
            base_url: str | None = None,
            timeout: float | None = None,
            transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # base_url 来自配置，调用方也可显式传入（测试 / 多环境）。
        self.base_url = (base_url or settings.docmind_base_url).rstrip("/")
        self.timeout = (
            timeout
            if timeout is not None
            else settings.docmind_timeout_seconds
        )
        # transport 只给测试注入 ASGI / MockTransport 用，运行时为 None。
        self._transport = transport

    async def _request(
            self,
            method: str,
            path: str,
            *,
            token: str,
            json: dict | None = None,
    ) -> httpx.Response:
        """发请求并把网络层错误统一成 DocMindUnavailableError。

        非 2xx 不在这里处理：404 在 get_document 里有独立语义，
        由具体方法按状态码区分。
        """
        try:
            async with httpx.AsyncClient(
                    base_url=self.base_url,
                    timeout=self.timeout,
                    transport=self._transport,
            ) as client:
                return await client.request(
                    method,
                    path,
                    json=json,
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.HTTPError as exc:
            logger.warning(
                "docmind request failed base_url=%s path=%s error=%s",
                self.base_url,
                path,
                exc,
            )
            raise DocMindUnavailableError(
                f"DocMind request failed: {exc}"
            ) from exc

    async def search(
            self,
            *,
            query: str,
            token: str,
            top_k: int = 5,
            document_id: int | None = None,
            mode: str = "vector",
    ) -> list[dict]:
        """调用 DocMind 的纯检索接口，返回片段列表（不含生成答案）。

        失败一律抛 DocMindUnavailableError，由调用方决定是转 502 还是
        把错误信息喂回模型——HTTP 细节不外泄给客户端。
        """
        payload: dict = {
            "query": query,
            "top_k": top_k,
            "mode": mode,
        }
        if document_id is not None:
            payload["document_id"] = document_id

        response = await self._request(
            "POST",
            "/retrieval/search",
            token=token,
            json=payload,
        )

        if response.status_code >= 400:
            logger.warning(
                "docmind returned %s base_url=%s",
                response.status_code,
                self.base_url,
            )
            raise DocMindUnavailableError(
                f"DocMind returned {response.status_code}"
            )

        return response.json().get("results", [])

    async def get_document(
            self,
            *,
            document_id: int,
            token: str,
    ) -> dict:
        """取文档元数据（文件名、索引状态等）。

        DocMind 对"不存在"和"不属于当前用户"统一返回 404，
        所以这里不区分两者——调用方也统一返回 404。
        """
        response = await self._request(
            "GET",
            f"/documents/{document_id}",
            token=token,
        )

        if response.status_code == 404:
            raise DocMindDocumentNotFoundError(
                f"document {document_id} not found"
            )
        if response.status_code >= 400:
            logger.warning(
                "docmind returned %s base_url=%s",
                response.status_code,
                self.base_url,
            )
            raise DocMindUnavailableError(
                f"DocMind returned {response.status_code}"
            )

        return response.json()


def get_docmind_client() -> DocMindClient:
    """FastAPI 依赖工厂：测试可以 override 成 fake。"""
    return DocMindClient()
