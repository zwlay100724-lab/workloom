"""RAG 应用服务：图的调用入口 + 会话持久化。

重构前后这个类的角色变了：
  之前 —— 它自己就是全部编排逻辑（取历史 → 改写检索 → 拼上下文 → 调链 →
          整理来源 → 存消息）。
  现在 —— 编排全在 app/graph 里，本类只剩「组装输入 → 交给图 → 存消息」。

为什么保留而不是删掉：eval/evaluate.py、eval/rag_chain_test.py 等评测脚本
依赖 RAGService(session).query(...) 这个入口。保留它，编排就只有图一份实现，
评测脚本一行都不用改。若删掉，同样的"存消息"逻辑还得在 api 层再写一遍。

【enable_rewrite 的含义变了】它原来控制整条检索链路是否走
retrieve_with_rewrite（改写 + 多查询）。现在它只控制一件事：tools_node
在**多轮**场景下是否先做一次确定性的 Query 改写（消解指代）。
单轮没有指代可消解，无论开关怎么设都不会调用 LLM —— 这是为了不给单轮
查询平白加一次模型调用。

依赖方向：api → services/rag_service → graph → services/retrieval_service
        → rag(纯组件) → repositories/models。单向，无环。
"""
import logging

from app.graph.workflow import RECURSION_LIMIT, graph
from app.llm.callbacks import UsageLoggingCallbackHandler, estimate_cost
from app.services.conversation_service import ConversationService

logger = logging.getLogger(__name__)


class RAGService:
    def __init__(self, session):
        self.session = session
        self.conversation_service = ConversationService(session)

    def query(
            self,
            question: str,
            user_id: int,
            document_id: int | None = None,
            top_k: int | None = None,
            conversation_id: int | None = None,
            enable_rewrite: bool = True,
    ) -> dict:
        """
        执行 RAG 问答（委托给 LangGraph 工作流）。

        Returns:
            {
                "answer": str,           # 生成的回答
                "sources": list[dict],   # 检索到的来源（与 schemas.SourceInfo 对齐）
                "context": str,          # 拼接后的上下文（调试用）
            }
        """
        # 1. 历史消息：由调用方查好传给图。图本身不碰会话存储，
        #    这样它不持有数据库副作用，也更容易单独测试。
        history = []
        if conversation_id:
            # get_history 内部会校验会话归属；不属于当前用户直接 404。
            history = self.conversation_service.get_history(
                conversation_id,
                user_id,
            )

        # 2. 交给图。messages 是对话主线：历史 + 当前问题一起喂进去。
        #    同时挂一个 usage callback：Agent 调用和 Query 改写都会走它。
        #    get_history() 返回的是 [{"role": ..., "content": ...}]，
        #    add_messages 会把它转成 HumanMessage / AIMessage，不用手工转换。
        #    session / user_id 是运行时依赖，走 configurable 注入 ——
        #    图是模块级单例，不能把它们绑在图对象上。
        usage_handler = UsageLoggingCallbackHandler()
        result = graph.invoke(
            {
                "messages": [*history, {"role": "user", "content": question}],
                "top_k": top_k,
                "document_id": document_id,
            },
            config={
                "configurable": {
                    "session": self.session,
                    "user_id": user_id,
                    # 多轮时是否做确定性 Query 改写（tools_node 读取）。
                    # 留成开关是为了能跑 A/B 对比，看改写到底有没有帮助。
                    "enable_rewrite": enable_rewrite,
                },
                "callbacks": [usage_handler],
                # agent ⇄ tools 是环，必须有上限兜底，否则模型反复检索会一直绕
                "recursion_limit": RECURSION_LIMIT,
            },
        )

        # 3. 保存本轮消息（副作用留在这一层，不放进图节点）
        if conversation_id:
            self.conversation_service.add_message(
                conversation_id,
                user_id,
                "user",
                question,
            )
            self.conversation_service.add_message(
                conversation_id,
                user_id,
                "assistant",
                result["answer"],
            )

        # 评测脚本需要"这次回答实际用了哪些片段"，不能在外部重新检索一遍：
        # 重新检索可能拿到不同结果，Ragas 的 context 指标就失真了。
        documents = result.get("documents") or []
        usage = usage_handler.summary()
        estimated_cost = estimate_cost(
            usage["input_tokens"],
            usage["output_tokens"],
        )
        logger.info(
            "rag query user_id=%s conversation_id=%s calls=%s "
            "input_tokens=%s output_tokens=%s total_tokens=%s "
            "estimated_cost_usd=%s",
            user_id,
            conversation_id,
            usage["calls"],
            usage["input_tokens"],
            usage["output_tokens"],
            usage["total_tokens"],
            estimated_cost,
        )

        return {
            "answer": result["answer"],
            "sources": result.get("sources") or [],
            "context": result.get("context") or "",
            "contexts": [doc.page_content for doc in documents],
            "usage": usage,
        }