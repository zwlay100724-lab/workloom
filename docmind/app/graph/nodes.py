"""LangGraph 节点。

原来的 question / router / retrieve / answer / general / no_answer 六个节点，
现在合并成一个 agent 循环。核心变化：**路由不再是流程图里的一条死分支，
而是模型自己决定的一步动作**。

    agent ─┬─(模型决定调工具)→ tools ─→ agent    ← ReAct 环
           └─(模型决定作答)──→ finalize → END

依赖（session / user_id）统一从 config["configurable"] 取，原因见 tools_node。
"""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from app.llm.client import get_chat_model
from app.rag.formatting import build_sources, format_context
from app.rag.prompts import AGENT_PROMPT
from app.rag.tools import (
    DOCMIND_TOOLS,
    SEARCH_DOCUMENTS,
    SEARCH_DOCUMENTS_HYBRID,
)
from app.services.retrieval_service import RetrievalService

from .state import AgentState

# 兜底话术：模型没给出内容时用（思考模型偶发返回空 content）
NO_ANSWER_TEXT = "抱歉，知识库中没有找到与您的问题相关的内容。"

# 模块级绑定。get_chat_model() 有 lru_cache，工具列表是静态的 ——
# 没有"每请求"的东西需要绑定（session/user_id 才走 config）。
_llm_with_tools = get_chat_model().bind_tools(DOCMIND_TOOLS)


def agent_node(state: AgentState, config: RunnableConfig) -> dict:
    """Agent 决策节点：决定调用工具，还是直接作答。

    这就是"升级后的 Router"。原来的 router_node 只能用一次 LLM 分类，
    在两条写死的分支里二选一；这里模型自己决定调哪个工具、调几次、
    什么时候收尾，而且能把检索结果读进来再决定下一步。

    系统提示词每次调用现拼，不写回 state —— 写回的话它会被 add_messages
    当成历史的一部分反复累积。
    """
    # 把 graph 的 config 继续传给模型，callbacks（usage 日志）才能生效。
    response = _llm_with_tools.invoke(
        [SystemMessage(content=AGENT_PROMPT), *state["messages"]],
        config=config,
    )

    if response.tool_calls:
        names = ", ".join(call["name"] for call in response.tool_calls)
        trace = f"agent_node: 决定调用 [{names}]"
    else:
        trace = "agent_node: 无需工具，直接作答"

    # 只返回新增的消息，由 add_messages 追加
    return {"messages": [response], "trace": [trace]}


def _split_conversation(messages: list) -> tuple[str, list[dict]]:
    """把 messages 拆成 (当前问题, 之前的历史)。

    从后往前找**最后一条 HumanMessage** —— 它才是本轮问题，它之前的都算历史。
    不能用 messages[-1]：Agent 循环中会不断追加 AIMessage / ToolMessage。

    历史只收 HumanMessage 和"没有 tool_calls 的 AIMessage"——带 tool_calls 的
    AIMessage 是中间过程，不是给用户看的回答，混进历史会污染改写结果。
    """
    for i in range(len(messages) - 1, -1, -1):
        if not isinstance(messages[i], HumanMessage):
            continue

        history: list[dict] = []
        for msg in messages[:i]:
            if isinstance(msg, HumanMessage):
                history.append({"role": "user", "content": msg.content})
            elif (
                isinstance(msg, AIMessage)
                and not getattr(msg, "tool_calls", None)
                and msg.content
            ):
                history.append({"role": "assistant", "content": msg.content})

        return messages[i].content or "", history

    return "", []


def tools_node(state: AgentState, config: RunnableConfig) -> dict:
    """执行 Agent 请求的工具调用。

    为什么不用内置的 ToolNode：见 app/rag/tools.py 顶部说明 ——
    ToolNode 只能产出 ToolMessage，检索到的结构化 documents 会丢，
    下游就产不出 sources。这里是自定义节点，能同时更新两个通道。

    session / user_id 从 config 取：图是模块级单例，而它们是每请求才有的值。
    """
    session = config["configurable"]["session"]
    user_id = config["configurable"]["user_id"]
    # 多轮改写开关。默认开；做 A/B 对比时可以在 invoke 时传 enable_rewrite=False。
    enable_rewrite = config["configurable"].get("enable_rewrite", True)

    # 请求级约束（调用方在 invoke 时给的，优先级高于模型自己填的参数）
    forced_top_k = state.get("top_k")
    forced_document_id = state.get("document_id")

    # Query 改写 / 多查询也会调用 LLM，把同一组 callbacks 继续传下去。
    callbacks = config.get("callbacks") or []
    retrieval = RetrievalService(callbacks=callbacks)
    messages = state["messages"]

    # 多轮才做确定性改写。单轮没有指代可消解，跳过省一次 LLM 调用。
    _, history = _split_conversation(messages)

    tool_messages: list[ToolMessage] = []
    documents = []
    rewrite_notes: list[str] = []
    used_hybrid = False

    for call in messages[-1].tool_calls:
        call_id = call["id"]
        name = call["name"]

        if name not in (SEARCH_DOCUMENTS, SEARCH_DOCUMENTS_HYBRID):
            tool_messages.append(ToolMessage(
                content=f"未知工具：{name}",
                tool_call_id=call_id,
            ))
            continue

        args = call.get("args") or {}
        query = (args.get("query") or "").strip()
        if not query:
            tool_messages.append(ToolMessage(
                content="检索失败：query 不能为空。",
                tool_call_id=call_id,
            ))
            continue

        # 检索前做一次确定性改写：把 Agent 传来的 query 结合历史补全指代。
        # Agent 自己也会改写（它填 query 参数），但那是概率性的；这一步是
        # 多轮场景下的确定性兜底。改写失败会内部回退原 query。
        if enable_rewrite and history:
            rewritten = retrieval.rewrite_query(query, history)
            if rewritten != query:
                rewrite_notes.append(f"{query!r}→{rewritten!r}")
                query = rewritten

        top_k = forced_top_k or args.get("top_k") or 5

        if name == SEARCH_DOCUMENTS_HYBRID:
            used_hybrid = True
            docs = retrieval.retrieve_hybrid(
                session,
                query,
                user_id=user_id,
                top_k=top_k,
                document_id=forced_document_id,
            )
        else:
            docs = retrieval.retrieve(
                session,
                query,
                user_id=user_id,
                top_k=top_k,
                document_id=forced_document_id,
            )

        documents.extend(docs)

        # 给模型读的文本。结构化 documents 另走一条通道写回 state。
        tool_messages.append(ToolMessage(
            content=format_context(docs) if docs else "（未检索到相关内容）",
            tool_call_id=call_id,
        ))

    trace = (
        f"tools_node: 检索 {len(tool_messages)} 次"
        f"（混合 {used_hybrid}），召回 {len(documents)} 条"
    )
    if rewrite_notes:
        trace += f"，改写 {'; '.join(rewrite_notes)}"

    return {
        "messages": tool_messages,
        "documents": documents,
        "trace": [trace],
    }


def finalize_node(state: AgentState) -> dict:
    """统一出口：把模型的最终回答和累积的检索来源整理成对外契约。

    单独一个节点而不是让 agent_node 顺带产出，是为了让"回答"和"来源"
    在一个确定的位置落地 —— agent_node 可能被调用很多次，只有最后一次
    才该产出输出。
    """
    documents = state.get("documents") or []
    sources = build_sources(documents) if documents else []
    answer = (state["messages"][-1].content or "").strip() or NO_ANSWER_TEXT

    return {
        "answer": answer,
        "sources": sources,
        "context": format_context(documents) if documents else "",
        "trace": [f"finalize_node: 来源 {len(sources)} 条"],
    }