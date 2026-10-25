"""
Query 改写模块：在检索前用 LLM 把用户问题改写成更适合检索的形式。
支持：查询重写、多查询生成。
"""
import json

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

# 策略1：查询重写 Prompt
REWRITE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是一个查询改写助手。请根据对话历史，将用户的当前问题改写成一个完整、独立、适合检索的问题。

改写规则：
1. 补充省略的指代信息（如"那""它""这个"等），结合历史理解用户真正在问什么
2. 去除口语化表达，使用规范书面语
3. 去掉与查询意图无关的背景叙述
4. 保留原始问题的核心意图，不要添加用户没问的信息
5. 如果原始问题已经完整清晰，直接返回原问题，不要强行改写
6. 只输出改写后的问题，不要输出解释或其他内容"""),
    ("human", """对话历史：
{history}

当前问题：{question}

改写后的问题："""),
])

# 策略3：多查询生成 Prompt
MULTI_QUERY_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是一个查询扩展助手。请为用户的问题生成 {num_queries} 个不同表述的变体，用于从多个角度检索。

要求：
1. 每个变体都是独立完整的问题
2. 变体之间表述不同，但核心意图一致
3. 用 JSON 数组格式输出，例如：["问题1", "问题2", "问题3"]
4. 只输出 JSON，不要输出其他内容"""),
    ("human", "用户问题：{question}"),
])


class QueryTransformer:
    def __init__(self, llm, callbacks: list | None = None):
        self.llm = llm
        rewrite_chain = REWRITE_PROMPT | llm | StrOutputParser()
        multi_query_chain = MULTI_QUERY_PROMPT | llm | StrOutputParser()
        if callbacks:
            rewrite_chain = rewrite_chain.with_config(callbacks=callbacks)
            multi_query_chain = multi_query_chain.with_config(
                callbacks=callbacks
            )
        self.rewrite_chain = rewrite_chain
        self.multi_query_chain = multi_query_chain

    def rewrite(self, question: str, history: list[dict] | None = None) -> str:
        """
        策略1：查询重写。把原始问题改写成更适合检索的独立问题。

        Args:
            question: 用户原始问题
            history: 对话历史，格式为 [{"role": "user"/"assistant", "content": "..."}]

        Returns:
            改写后的问题
        """
        # 格式化历史
        if history:
            history_text = "\n".join([
                f"{'用户' if m['role'] == 'user' else '助手'}：{m['content']}"
                for m in history[-6:]  # 只取最近3轮
            ])
        else:
            history_text = "（无历史）"

        rewritten = self.rewrite_chain.invoke({
            "question": question,
            "history": history_text,
        })
        return rewritten.strip()

    def generate_multi_queries(self, question: str, num_queries: int = 3) -> list[str]:
        """
        策略3：多查询生成。生成多个表述变体，分别检索后合并。

        Returns:
            问题列表（包含原始问题 + 生成的变体）
        """
        try:
            result = self.multi_query_chain.invoke({
                "question": question,
                "num_queries": num_queries,
            })
            # 解析 JSON
            queries = json.loads(result.strip())
            # 确保原始问题在列表中
            if question not in queries:
                queries.insert(0, question)
            return queries[:num_queries + 1]
        except (json.JSONDecodeError, Exception):
            # 解析失败时退化为只用原始问题
            return [question]