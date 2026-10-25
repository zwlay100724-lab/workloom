"""（已停用）LCEL 链。

这里原来有 build_rag_chain / build_general_chain，把 prompt | llm | parser
组合成链，由 answer_node / general_node 调用。

合并成 Agent 图之后，生成不再走 LCEL 链：Agent 直接拿 state["messages"]
调模型（见 app/graph/nodes.py 的 agent_node），系统提示词由
app/rag/prompts.AGENT_PROMPT 提供，也不再需要 {context} / {history} 占位符
那套模板装配。所以这两个链连同它们依赖的 RAG_PROMPT / GENERAL_PROMPT 一并
移除了。

文件先留着，作为将来需要 LCEL 链（例如做评估、加后处理管线）时的落点；
确认不需要可以整个删掉。
"""