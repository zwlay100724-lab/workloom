"""
Rerank 重排序：对粗召回的片段用 Cross-Encoder 精排。
这里用 API 方式（如 Cohere 或通义 Rerank），也可以换成本地模型。
"""
from typing import Optional

from langchain_core.documents import Document


class Reranker:
    def __init__(self, api_key: str, model: str = "bge-reranker-large", base_url: Optional[str] = None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    def rerank(self, query: str, documents: list[Document], top_k: int = 5) -> list[Document]:
        """
        对检索到的文档重排序，返回 Top-K。

        实际实现需要调用 Rerank API。这里给出接口框架，
        具体 API 调用根据你选择的服务商填写。
        """
        if len(documents) <= top_k:
            return documents  # 数量不够，不用重排

        # 伪代码：调用 Rerank API
        # response = rerank_api.rerank(
        #     model=self.model,
        #     query=query,
        #     documents=[d.page_content for d in documents],
        #     top_n=top_k,
        # )
        #
        # results = []
        # for item in response.results:
        #     doc = documents[item.index]
        #     doc.metadata["rerank_score"] = item.relevance_score
        #     results.append(doc)
        # return results

        # 占位：不做实际重排，直接返回前 top_k（接入 API 后替换）
        return documents[:top_k]