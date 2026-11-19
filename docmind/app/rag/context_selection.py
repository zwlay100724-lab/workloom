"""最终上下文选择：全局 Top-N + 每文档上限 + 近似去重。

Agent 可能在多轮里反复检索，state 里的 documents 是并集。
如果直接把这些片段全交给回答/评测，context precision 会被无关片段拉低。
这里用「分数排序 + 每文档配额 + 字符 n-gram 相似度去重」做一次轻量 MMR：
- 分数高的优先；
- 同一文档最多保留若干条；
- 与已选片段高度相似的跳过，保证上下文多样性。
"""
import re
from collections import Counter

from langchain_core.documents import Document


def document_score(document: Document) -> float:
    """取检索/精排分数，越大越好；没有分数时返回 0。"""
    metadata = document.metadata or {}
    for key in (
        "rerank_score",
        "retrieval_score",
        "rrf_score",
        "similarity",
    ):
        value = metadata.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue

    distance = metadata.get("distance")
    if distance is not None:
        try:
            # 向量距离越小越好，取负值转成“越大越好”。
            return -float(distance)
        except (TypeError, ValueError):
            pass
    return 0.0


def _shingles(text: str, n: int = 3) -> set[str]:
    text = re.sub(r"\s+", "", text or "")
    if not text:
        return set()
    if len(text) <= n:
        return {text}
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def _similarity(left: str, right: str) -> float:
    """字符 3-gram Jaccard，适合中文且不依赖额外模型。"""
    left_set = _shingles(left)
    right_set = _shingles(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def select_documents(
        documents: list[Document],
        *,
        max_total: int = 8,
        max_per_document: int = 3,
        duplicate_threshold: float = 0.85,
) -> list[Document]:
    """按分数选最终上下文，控制总量、单文档数量和重复度。"""
    if max_total <= 0:
        return list(documents)

    ordered = sorted(
        documents,
        key=document_score,
        reverse=True,
    )
    selected: list[Document] = []
    per_document: Counter = Counter()
    exact_keys: set[tuple] = set()

    for document in ordered:
        metadata = document.metadata or {}
        document_id = metadata.get("document_id")
        chunk_index = metadata.get("chunk_index")
        if document_id is None and chunk_index is None:
            exact_key = (id(document),)
            bucket = f"__object_{id(document)}"
        else:
            exact_key = (document_id, chunk_index)
            bucket = str(document_id)
        if exact_key in exact_keys:
            continue

        if per_document[bucket] >= max_per_document:
            continue

        if duplicate_threshold > 0 and any(
            _similarity(document.page_content, picked.page_content)
            >= duplicate_threshold
            for picked in selected
        ):
            continue

        exact_keys.add(exact_key)
        per_document[bucket] += 1
        selected.append(document)

        if len(selected) >= max_total:
            break

    return selected
