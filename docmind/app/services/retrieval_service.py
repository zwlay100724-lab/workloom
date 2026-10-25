import logging

from langchain_core.documents import Document

from app.core.config import settings
from app.llm.client import get_chat_model
from app.rag.embeddings import get_embeddings
from app.rag.query_transformer import QueryTransformer
from app.repositories.vector_repository import VectorRepository

logger = logging.getLogger(__name__)


class RetrievalService:
    """
    检索服务：问题 → 向量化 → 向量检索 → 返回 Document 列表。
    封装了 Embedding + VectorRepository，对外提供统一的检索接口。

    为什么 session 是"方法参数"而不是"构造参数"：
    这个类现在是纯粹的能力封装，不持有任何会话生命周期。session 属于
    "这一次检索用哪个连接"，在图里由每次 invoke 决定；绑到实例上会让
    服务对象和某个具体连接耦合。改完之后本类无参构造、可安全复用。

    format_context() 已移到 app/rag/formatting.py —— 它不碰 self，
    属于纯转换，且 graph 节点也要用同一份实现。
    """

    def __init__(self, callbacks: list | None = None):
        self.embeddings = get_embeddings()
        self.llm = get_chat_model()
        self.query_transformer = QueryTransformer(
            self.llm,
            callbacks=callbacks,
        )

    def retrieve(
            self,
            session,
            question: str,
            user_id: int,
            top_k: int | None = None,
            document_id: int | None = None,
    ) -> list[Document]:
        """
        根据问题检索相关文档片段。

        Args:
            session: 数据库会话
            question: 用户问题
            user_id: 当前用户ID（强制过滤，确保用户隔离）
            top_k: 返回数量，默认用配置中的 TOP_K
            document_id: 可选，限定在某个文档中搜索

        Returns:
            list[Document]: 相关片段列表，按相似度从高到低排序
        """
        if top_k is None:
            top_k = settings.TOP_K

        # 1. 问题向量化
        query_vector = self.embeddings.embed_query(question)

        # 2. 向量检索（带 user_id 过滤）
        results = VectorRepository(session).search_similar(
            query_embedding=query_vector,
            user_id=user_id,
            top_k=top_k,
            document_id=document_id,
        )

        # 3. 转回 LangChain Document 对象（保留 metadata，方便后续使用）
        documents = []
        for chunk, distance in results:
            doc = Document(
                page_content=chunk.content,
                metadata={
                    "source": chunk.source,
                    "page": chunk.page,
                    "chunk_index": chunk.chunk_index,
                    "document_id": chunk.document_id,
                    "distance": distance,  # 距离，越小越相似
                    "similarity": 1 - distance,  # 相似度，越大越相似
                }
            )
            documents.append(doc)

        return documents

    def rewrite_query(self, question: str, history: list[dict] | None) -> str:
        """确定性 Query 改写：结合对话历史把指代（"它""那个"）补全成独立问题。

        为什么单独暴露这个方法而不是只在 retrieve_with_rewrite 里用：
        Agent 链路下检索由 graph 的 tools_node 直接调用，需要独立调用改写。
        把「调用 + 失败回退」收敛在这一处，两条链路共用同一份行为。

        改写失败一律回退原问题 —— 改写是锦上添花，绝不能让检索因 LLM 失败而崩。
        """
        if not history:
            return question

        try:
            rewritten = self.query_transformer.rewrite(question, history)
        except Exception as exc:  # noqa: BLE001 — 改写失败不能阻塞检索
            logger.warning(
                "query rewrite failed question=%r error=%s",
                question,
                exc,
            )
            return question

        if rewritten and rewritten != question:
            logger.info(
                "query rewritten original=%r rewritten=%r",
                question,
                rewritten,
            )
            return rewritten

        return question

    def retrieve_hybrid(
            self,
            session,
            question: str,
            user_id: int,
            top_k: int | None = None,
            document_id: int | None = None,
            candidate_k: int = 20,
    ) -> list[Document]:
        """
        混合检索：向量检索 + 关键词(PostgreSQL 全文)检索，RRF 融合排序。

        相对纯向量 retrieve()，对"标题/术语/精确短语"类问题（比如"板块二的标题
        是什么"）命中更准——纯向量常被语义居中但没答案的大纲段落压过。

        Args:
            session: 数据库会话
            question: 用户问题（同时用于向量化和全文检索原文）
            user_id: 当前用户ID
            top_k: 返回数量，默认用配置中的 TOP_K
            document_id: 可选，限定在某个文档中搜索
            candidate_k: 每路(向量/关键词)先粗召回多少，默认 20

        Returns:
            list[Document]: 按 RRF 分数从高到低排序。
                注意：与 retrieve() 不同，这里 metadata 里没有 similarity
                （RRF 融合分不是相似度），改用 rrf_score 字段。
        """
        if top_k is None:
            top_k = settings.TOP_K

        # 1. 问题向量化（关键词一路直接用问题原文，不用再向量化）
        query_vector = self.embeddings.embed_query(question)

        # 2. 混合检索（带 user_id / 可选 document_id 过滤）
        results = VectorRepository(session).hybrid_search(
            query=question,
            query_embedding=query_vector,
            user_id=user_id,
            top_k=top_k,
            document_id=document_id,
            candidate_k=candidate_k,
        )

        # 3. 转回 LangChain Document 对象
        documents = []
        for chunk, rrf_score in results:
            doc = Document(
                page_content=chunk.content,
                metadata={
                    "source": chunk.source,
                    "page": chunk.page,
                    "chunk_index": chunk.chunk_index,
                    "document_id": chunk.document_id,
                    "rrf_score": rrf_score,  # 越大越靠前
                }
            )
            documents.append(doc)

        return documents

    def retrieve_with_rewrite(
            self,
            session,
            question: str,
            user_id: int,
            top_k: int | None = None,
            document_id: int | None = None,
            history: list[dict] | None = None,
            enable_rewrite: bool = False,
            enable_multi_query: bool = False,
            num_queries: int = 3,
    ) -> list[Document]:
        """
        检索 + Query 改写 / 多查询扩展（板块九）。

        与纯净的 retrieve() 的区别：检索前先用 LLM 生成更适合检索的查询——
          - 策略1 查询重写：多轮带历史时，把"它/那个"这类指代补全成独立完整的问题；
          - 策略3 多查询：生成多个表述变体分别检索再合并，从多角度召回（更耗 LLM）。

        设计要点：retrieve() 保持纯检索不被污染；改写/扩展都是"锦上添花"，
        出错一律回退原问题，绝不让检索因 LLM 失败而崩。

        Args:
            session: 数据库会话
            question: 用户问题
            user_id / document_id / top_k: 同 retrieve()
            history: 对话历史 [{"role": "user"|"assistant", "content": ...}]，
                     多轮指代消解用
            enable_rewrite: 有历史时把问题改写为完整独立的问题
            enable_multi_query: 生成多个表述分别检索后合并
            num_queries: 生成几个变体（最终返回含原问题共 num_queries+1 条候选）

        Returns:
            list[Document]: 与 retrieve() 同构（metadata 含 similarity），
                多查询时已去重、按相似度降序取 top_k。
        """
        if top_k is None:
            top_k = settings.TOP_K

        # ===== 1. 生成候选查询 =====
        search_queries = [question]

        # 策略1：查询重写（只有多轮带历史才需要，改写指代）
        if enable_rewrite and history:
            search_queries = [self.rewrite_query(question, history)]

        # 策略3：多查询生成（在改写后的问题上扩展）
        if enable_multi_query:
            search_queries = self.query_transformer.generate_multi_queries(
                search_queries[0], num_queries=num_queries
            )
            logger.info(
                "multi query expanded queries=%s",
                search_queries,
            )

        # ===== 2. 检索 =====
        # 单一查询（未改写或只改写）直接走纯净 retrieve()，行为完全一致
        if len(search_queries) == 1:
            return self.retrieve(session, search_queries[0], user_id, top_k, document_id)

        # 多查询：每路先多取一些，给去重留余量，最后统一取 top_k
        per_query_k = max(top_k, 20)
        vector_repo = VectorRepository(session)
        all_pairs = []  # [(chunk, distance), ...]
        for q in search_queries:
            query_vector = self.embeddings.embed_query(q)
            results = vector_repo.search_similar(
                query_embedding=query_vector,
                user_id=user_id,
                top_k=per_query_k,
                document_id=document_id,
            )
            all_pairs.extend(results)

        # ===== 3. 去重：同一 chunk 保留最小距离（最高相似度）的那条 =====
        best = {}  # chunk.id -> (distance, chunk)
        for chunk, distance in all_pairs:
            if chunk.id not in best or distance < best[chunk.id][0]:
                best[chunk.id] = (distance, chunk)

        # ===== 4. 转 Document，按相似度降序取 top_k =====
        documents = []
        for distance, chunk in best.values():  # best 存的是 (distance, chunk)
            documents.append(Document(
                page_content=chunk.content,
                metadata={
                    "source": chunk.source,
                    "page": chunk.page,
                    "chunk_index": chunk.chunk_index,
                    "document_id": chunk.document_id,
                    "distance": distance,
                    "similarity": 1 - distance,
                }
            ))
        documents.sort(key=lambda d: d.metadata["similarity"], reverse=True)

        return documents[:top_k]