"""供 Agent 调用的工具。

【为什么只声明 schema，执行放在 graph 的 tools_node？】
LangGraph 的节点能一次更新多个 state 通道。检索产生两份产物：
  1. 给模型读的上下文文本（塞进 ToolMessage）
  2. 要写回 state 的结构化 documents（用于 sources 溯源）
内置 ToolNode 只能把工具的返回值塞进一条 ToolMessage，第 2 份会被压扁成
字符串，sources 就丢了。所以 schema 在这里给 LLM 看，执行在 tools_node 里做，
那里可以同时更新 messages 和 documents 两个通道。

schema 用 OpenAI function 的 dict 格式（和 taskflow 的 TASKFLOW_TOOLS 一致），
bind_tools 直接接受，不必再包一层 StructuredTool。

【工具编写原则】沿用 taskflow tools.py 的三条：
只做参数转发、不写业务逻辑、不直接操作数据库 —— 业务在 RetrievalService。
"""

SEARCH_DOCUMENTS_TOOL = {
    "type": "function",
    "function": {
        "name": "search_documents",
        "description": (
            "在企业知识库中检索文档片段。当用户问题涉及公司制度、规章、流程、"
            "员工政策、内部文档或已上传的资料时，必须先调用本工具检索，再基于"
            "检索结果回答。闲聊、天气、通用常识、编程问题等不需要调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "检索用的查询语句。可以对用户原问题做改写或补充指代，"
                        "以便更准确地命中。"
                    ),
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回的文档片段数量，默认 5。",
                },
            },
            "required": ["query"],
        },
    },
}

SEARCH_DOCUMENTS_HYBRID_TOOL = {
    "type": "function",
    "function": {
        "name": "search_documents_hybrid",
        "description": (
            "混合检索：语义检索 + 关键词精确匹配（Postgres 全文 + 三元组子串），"
            "两路结果用 RRF 融合排序。当问题里含**具体术语、标题、编号、专有名词**"
            "（例如「板块七」「RetrievalQA」「pgvector」「第 3 步」）时，比单纯"
            "语义检索更容易命中原文。用自然语言描述的普通问题仍用 search_documents。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "检索用的查询语句。混合检索的关键词一路直接用原文匹配，"
                        "所以**不要把术语改写成同义词**，保留原词更好命中。"
                    ),
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回的文档片段数量，默认 5。",
                },
            },
            "required": ["query"],
        },
    },
}

DOCMIND_TOOLS = [SEARCH_DOCUMENTS_TOOL, SEARCH_DOCUMENTS_HYBRID_TOOL]

# 工具名常量：tools_node 里按名字分发，避免裸字符串散落
SEARCH_DOCUMENTS = "search_documents"
SEARCH_DOCUMENTS_HYBRID = "search_documents_hybrid"