"""组装并编译 LangGraph 工作流。

图的形状：

    START → agent ─┬─(模型决定调工具)→ tools ─┬─→ agent     ← ReAct 环
                   │                          ╰──────────────╯
                   └─(模型决定作答)→ finalize → END

和上一版（router 二选一 + 固定分支）的区别：
  - 没有"意图分类"这一步了，模型直接决定要不要调工具、调哪个、调几次；
  - 检索不再是必经之路，而是模型的一个可选动作；
  - tools → agent 是**环**，所以调用方要设 recursion_limit（见下）。
"""
from typing import Literal

from langgraph.graph import END, START, StateGraph

from .nodes import agent_node, finalize_node, tools_node
from .state import AgentState


def should_continue(state: AgentState) -> Literal["tools", "finalize"]:
    """条件边：模型这一轮是要调工具，还是已经给出最终回答。

    这是 ReAct 循环的出口判断 —— 依据是最后一条 AIMessage 上有没有 tool_calls。
    """
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else "finalize"


def build_graph():
    builder = StateGraph(AgentState)

    builder.add_node("agent", agent_node)
    builder.add_node("tools", tools_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "agent")

    builder.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", "finalize": "finalize"},
    )

    # ReAct 环：工具执行完把结果交回 agent，由它决定继续调还是收尾
    builder.add_edge("tools", "agent")

    builder.add_edge("finalize", END)

    return builder.compile()


# 模块级单例：编译一次、全程复用。
# 运行时依赖（session / user_id）走 config["configurable"]，见 nodes.tools_node。
graph = build_graph()

# ReAct 环的迭代上限。模型如果陷入"反复检索"会一直绕，
# 撞到上限时 LangGraph 抛 GraphRecursionError 而不是死循环。
# agent + tools 一轮算 2 步，所以 12 ≈ 最多 5 轮工具调用。
RECURSION_LIMIT = 12