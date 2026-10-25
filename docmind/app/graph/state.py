from operator import add
from typing import Annotated

from langchain_core.documents import Document
from langgraph.graph import MessagesState


def merge_documents(old: list[Document], new: list[Document]) -> list[Document]:
    """跨多轮检索累加文档，按 (document_id, chunk_index) 去重。

    Agent 可能连续调几次 search_documents：
      - 用默认的 last-write-wins，后一次检索会把前一次的结果整个冲掉，
        sources 只剩最后一次的；
      - 用 operator.add 又会产生重复片段。
    所以这里做「累加 + 去重」。
    """
    merged = list(old)
    seen = {
        (doc.metadata.get("document_id"), doc.metadata.get("chunk_index"))
        for doc in merged
    }

    for doc in new:
        key = (doc.metadata.get("document_id"), doc.metadata.get("chunk_index"))
        if key not in seen:
            seen.add(key)
            merged.append(doc)

    return merged


class AgentState(MessagesState):
    """统一状态：messages 是对话主线，其余是领域字段。

    为什么继承 MessagesState 而不是手写 TypedDict：
    它本身的定义就是 ``messages: Annotated[list[AnyMessage], add_messages]``，
    手写一遍等于把 LangGraph 已经给的东西抄一次。

    【易错点】节点只返回本次新增的消息，例如 ``{"messages": [response]}``，
    绝不要返回完整 messages —— add_messages 这个 reducer 负责追加，
    返回全量会让历史重复堆积。
    """

    # 检索到的片段。Agent 多轮调用工具时累加去重。
    documents: Annotated[list[Document], merge_documents]

    # 请求级检索参数（调用方传入，tools_node 用来约束检索范围）
    top_k: int | None
    document_id: int | None

    # ---------- 对外输出（与 schemas.rag.RAGQueryResponse 对齐） ----------
    answer: str
    sources: list[dict]

    # 调试用：拼给模型看的上下文
    context: str

    # 可观测性：各节点的执行轨迹。add reducer 累加，默认会被覆盖。
    trace: Annotated[list[str], add]