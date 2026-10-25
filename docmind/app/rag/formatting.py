"""检索结果 → (prompt 上下文 / 对外来源列表) 的纯函数。

为什么单独抽一层：
- 这两个转换原先分散在两处 —— format_context 挂在 RetrievalService 上
  （但它根本不碰 self），来源整理内联在 RAGService.query 里；
- 现在 LangGraph 的 answer_node 也要产出来源，各写各的就是第三份实现。
收敛到一处后，改格式只改这个文件。

放在 rag/ 而不是 graph/：它是无状态的纯转换，属于"RAG 构建块"，
不感知 session / 用户 / 图，符合 rag/ 这一层的定位（只依赖 core.config）。
"""
from pathlib import Path

from langchain_core.documents import Document


def _display_source(source: str | None) -> str:
    """只返回文件名，不把服务器绝对路径暴露给 API / prompt。"""
    return Path(source).name if source else "未知"


def _similarity_of(doc: Document) -> float:
    """取相似度。

    向量检索写入 similarity；混合检索写入的是 rrf_score（量纲不同，
    不做换算，仅作兜底，避免元数据缺失时整个接口崩掉）。
    """
    metadata = doc.metadata
    value = metadata.get("similarity")
    if value is None:
        value = metadata.get("rrf_score", 0.0)
    return float(value)


def _page_display(page: int | None) -> str:
    """页码对外展示：库里存的是 0 基，展示给人看的要 +1。"""
    return f"第{page + 1}页" if page is not None else "未知页"


def format_context(documents: list[Document]) -> str:
    """把检索到的 Document 列表拼成 prompt 里的 {context}。"""
    if not documents:
        return "（未检索到相关内容）"

    parts = []
    for i, doc in enumerate(documents):
        parts.append(
            f"[片段{i + 1} | 来源: {_display_source(doc.metadata.get('source'))} "
            f"| {_page_display(doc.metadata.get('page'))} "
            f"| 相似度: {_similarity_of(doc):.3f}]\n"
            f"{doc.page_content}"
        )

    return "\n\n---\n\n".join(parts)


def build_sources(documents: list[Document], snippet_length: int = 150) -> list[dict]:
    """把检索结果整理成 API 的 sources 字段。

    字段与 app.schemas.rag.SourceInfo 严格对齐 —— 之前这里产出的键是
    page_number，而 schema 要的是 page_display，导致 /rag/query 每次
    响应都在 pydantic 校验阶段报错。现在两边同名。
    """
    sources: list[dict] = []
    seen: set[tuple] = set()  # 同一文档同一页只保留一条来源

    for doc in documents:
        metadata = doc.metadata
        page = metadata.get("page")
        source = _display_source(metadata.get("source"))
        key = (source, page)
        if key in seen:
            continue
        seen.add(key)

        sources.append({
            "document": source,
            "page": page,
            "page_display": _page_display(page),
            "chunk_index": metadata.get("chunk_index"),
            "similarity": round(_similarity_of(doc), 4),
            "snippet": doc.page_content[:snippet_length].strip() + "...",
        })

    return sources