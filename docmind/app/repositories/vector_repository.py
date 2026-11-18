from sqlalchemy import text
from sqlmodel import Session, select

from app.models.document_chunk import DocumentChunk
from app.rag.chunk_identity import compute_chunk_uid

# RRF 常数与各路权重。向量一路语义质量更高，词法一路只作为补充；
# 调参时可以通过 hybrid_search 的参数覆盖（评测脚本用）。
RRF_K = 60
VECTOR_ROUTE_WEIGHT = 1.0
LEXICAL_ROUTE_WEIGHT = 0.5


def _combine_lexical_ranks(
        ranks_by_route: list[dict[int, int]],
) -> dict[int, int]:
    """同一 chunk 在多个词法路线里只保留最好的名次。

    tsvector 和关键词两路高度重叠；如果不合并，一个 chunk 同时命中
    两路会拿到两份 RRF 分，轻易压过语义更相关的片段。
    """
    merged: dict[int, int] = {}
    for ranks in ranks_by_route:
        for doc_id, rank in ranks.items():
            current = merged.get(doc_id)
            if current is None or rank < current:
                merged[doc_id] = rank
    return merged


def _rrf_scores(
        vector_ranks: dict[int, int],
        lexical_ranks: dict[int, int],
        *,
        vector_weight: float = VECTOR_ROUTE_WEIGHT,
        lexical_weight: float = LEXICAL_ROUTE_WEIGHT,
        k: int = RRF_K,
) -> dict[int, float]:
    """向量 + 单路词法的加权 RRF。"""
    scores: dict[int, float] = {}
    for doc_id in set(vector_ranks) | set(lexical_ranks):
        score = 0.0
        if doc_id in vector_ranks:
            score += vector_weight / (k + vector_ranks[doc_id])
        if doc_id in lexical_ranks:
            score += lexical_weight / (k + lexical_ranks[doc_id])
        scores[doc_id] = score
    return scores


class VectorRepository:
    def __init__(self, session: Session):
        self.session = session

    def add_chunks(
            self,
            chunks: list,  # list[Document]，来自 split_documents
            vectors: list[list[float]],  # 来自 embeddings.embed_documents
            document_id: int,
            user_id: int,
    ) -> None:
        """批量插入片段 + 向量。

        不 commit：由调用方把"删旧 chunk + 写新 chunk + 更新文档状态"
        放在同一个事务里，保证索引替换是原子的。
        """
        if len(chunks) != len(vectors):
            raise ValueError(
                f"chunks/vectors 数量不一致: {len(chunks)} != {len(vectors)}"
            )

        for chunk, vector in zip(chunks, vectors):
            record = DocumentChunk(
                document_id=document_id,
                user_id=user_id,
                content=chunk.page_content,
                # 内容不变 → uid 不变；重建索引只需要给新片段重新打 uid。
                chunk_uid=compute_chunk_uid(chunk.page_content),
                chunk_index=chunk.metadata.get("chunk_index", 0),
                page=chunk.metadata.get("page"),
                source=chunk.metadata.get("source"),
                embedding=vector,
            )
            self.session.add(record)

        # flush 让唯一约束 (document_id, chunk_index) 尽早暴露；
        # 真正的提交由上层事务负责。
        self.session.flush()

    def search_similar(
            self,
            query_embedding: list[float],
            user_id: int,
            top_k: int = 5,
            document_id: int | None = None,
    ) -> list[tuple[DocumentChunk, float]]:
        """
        相似度检索，返回 (片段, 距离) 列表，按距离从小到大排序。
        支持 user_id 过滤和可选的 document_id 过滤。
        """
        # 用原生 SQL 做向量排序（pgvector 操作符）；HNSW 索引匹配 <=>。
        sql = """
            SELECT id, embedding <=> :query_vec AS distance
            FROM document_chunks
            WHERE user_id = :user_id
        """
        params = {
            "query_vec": str(query_embedding),  # pgvector 接受字符串形式的向量
            "user_id": user_id,
        }

        if document_id is not None:
            sql += " AND document_id = :doc_id"
            params["doc_id"] = document_id

        sql += " ORDER BY embedding <=> :query_vec LIMIT :top_k"
        params["top_k"] = top_k

        rows = self.session.execute(text(sql), params).fetchall()
        if not rows:
            return []

        # 批量取回 ORM 对象，避免逐行 session.get 的 N+1。
        ids = [row.id for row in rows]
        chunks = {
            chunk.id: chunk
            for chunk in self.session.exec(
                select(DocumentChunk).where(DocumentChunk.id.in_(ids))
            ).all()
        }
        return [
            (chunks[row.id], row.distance)
            for row in rows
            if row.id in chunks
        ]

    def delete_by_document(self, document_id: int, user_id: int) -> int:
        """删除某个文档的所有向量。

        不 commit——由上层 DocumentService 在事务末尾统一提交。
        """
        sql = """
            DELETE FROM document_chunks
            WHERE document_id = :document_id
              AND user_id = :user_id
        """
        result = self.session.execute(text(sql), {
            "document_id": document_id,
            "user_id": user_id,
        })
        return result.rowcount

    def hybrid_search(
            self,
            query: str,
            query_embedding: list[float],
            user_id: int,
            keywords: list[str] | None = None,
            top_k: int = 5,
            document_id: int | None = None,
            candidate_k: int = 20,  # 每路召回数量
            vector_weight: float = VECTOR_ROUTE_WEIGHT,
            lexical_weight: float = LEXICAL_ROUTE_WEIGHT,
            merge_lexical_routes: bool = True,
    ) -> list[tuple]:
        """
        混合检索：向量 + 词法（tsvector / 关键词）+ 加权 RRF。

          - 向量一路：语义检索（中文长句最稳，走 HNSW）；
          - tsvector 一路：Postgres 全文（英文/术语 token，中文不分词）；
          - 关键词一路：用查询侧抽出的关键词做中文/精确子串 + trigram 模糊匹配，
            命中关键词越多排越前。

        默认把 tsvector 和关键词两路合并成一个“词法路”：同一 chunk
        只取两路里更好的名次，避免重复加分。`merge_lexical_routes=False`
        保留旧的三路独立计数行为，供 A/B 评测使用。

        为什么关键词一路不用纯 word_similarity：
        pg_trgm 对中文不生成 trigram（show_trgm('板块二') 返回空），
        只靠 word_similarity 会让中文关键词召回直接失效。

        keywords 为空时退回整句匹配，保持旧行为。
        """

        # 1. 向量检索（粗排，取 candidate_k 个）
        vec_sql = """
            SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> :qvec) AS rank
            FROM document_chunks
            WHERE user_id = :uid
        """
        vec_params = {
            "qvec": str(query_embedding),
            "uid": user_id,
            "ck": candidate_k,
        }
        if document_id is not None:
            vec_sql += " AND document_id = :doc_id"
            vec_params["doc_id"] = document_id
        vec_sql += " ORDER BY embedding <=> :qvec LIMIT :ck"
        vec_rows = self.session.execute(text(vec_sql), vec_params).fetchall()
        vec_ranks = {row.id: row.rank for row in vec_rows}

        # 2. tsvector 全文检索（PostgreSQL 内置 parser）
        kw_sql = """
            SELECT id, ROW_NUMBER() OVER (
                ORDER BY ts_rank(
                    to_tsvector('simple', content),
                    plainto_tsquery('simple', :q)
                ) DESC
            ) AS rank
            FROM document_chunks
            WHERE user_id = :uid
              AND to_tsvector('simple', content) @@ plainto_tsquery('simple', :q)
        """
        kw_params = {"q": query, "uid": user_id, "ck": candidate_k}
        if document_id is not None:
            kw_sql += " AND document_id = :doc_id"
            kw_params["doc_id"] = document_id
        kw_sql += " LIMIT :ck"
        kw_rows = self.session.execute(text(kw_sql), kw_params).fetchall()
        kw_ranks = {row.id: row.rank for row in kw_rows}

        # 3. 中文/精确子串 + trigram 模糊匹配
        #    用查询侧抽出的关键词任意命中；命中词数越多排越前。
        match_terms = keywords or [query]
        tg_sql = """
            WITH scored AS (
                SELECT id,
                       (
                           SELECT count(*)
                           FROM unnest(CAST(:match_terms AS text[])) AS term
                           WHERE position(lower(term) in lower(content)) > 0
                       ) AS hit_count,
                       (
                           SELECT coalesce(
                               max(word_similarity(term, content)), 0
                           )
                           FROM unnest(CAST(:match_terms AS text[])) AS term
                       ) AS max_similarity
                FROM document_chunks
                WHERE user_id = :uid
                  AND EXISTS (
                      SELECT 1
                      FROM unnest(CAST(:match_terms AS text[])) AS term
                      WHERE position(lower(term) in lower(content)) > 0
                         OR word_similarity(term, content) > 0.1
                  )
        """
        tg_params = {
            "uid": user_id,
            "match_terms": match_terms,
            "ck": candidate_k,
        }
        if document_id is not None:
            tg_sql += " AND document_id = :doc_id"
            tg_params["doc_id"] = document_id
        tg_sql += """
            )
            SELECT id, ROW_NUMBER() OVER (
                ORDER BY hit_count DESC, max_similarity DESC, id
            ) AS rank
            FROM scored
            ORDER BY hit_count DESC, max_similarity DESC, id
            LIMIT :ck
        """
        tg_rows = self.session.execute(text(tg_sql), tg_params).fetchall()
        tg_ranks = {row.id: row.rank for row in tg_rows}

        # 4. RRF 融合：向量 + 词法（合并后）加权倒数和。
        if merge_lexical_routes:
            lexical_ranks = _combine_lexical_ranks([kw_ranks, tg_ranks])
            rrf_scores = _rrf_scores(
                vec_ranks,
                lexical_ranks,
                vector_weight=vector_weight,
                lexical_weight=lexical_weight,
            )
        else:
            # 旧行为：tsvector / 关键词两路独立计数，供 A/B 评测。
            rrf_scores = {}
            all_ids = set(vec_ranks) | set(kw_ranks) | set(tg_ranks)
            for doc_id in all_ids:
                score = 0.0
                if doc_id in vec_ranks:
                    score += vector_weight / (RRF_K + vec_ranks[doc_id])
                if doc_id in kw_ranks:
                    score += lexical_weight / (RRF_K + kw_ranks[doc_id])
                if doc_id in tg_ranks:
                    score += lexical_weight / (RRF_K + tg_ranks[doc_id])
                rrf_scores[doc_id] = score

        sorted_ids = sorted(
            rrf_scores,
            key=rrf_scores.get,
            reverse=True,
        )[:top_k]
        if not sorted_ids:
            return []

        # 批量取回，避免逐条 session.get。
        chunks = {
            chunk.id: chunk
            for chunk in self.session.exec(
                select(DocumentChunk).where(
                    DocumentChunk.id.in_(sorted_ids)
                )
            ).all()
        }
        return [
            (chunks[doc_id], rrf_scores[doc_id])
            for doc_id in sorted_ids
            if doc_id in chunks
        ]
