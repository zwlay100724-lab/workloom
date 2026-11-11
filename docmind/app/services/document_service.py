import logging
from datetime import datetime

from sqlalchemy import delete as sa_delete
from sqlmodel import Session, func, select

from app.core.exceptions import (
    ChunkNotFoundError,
    DocumentBusyError,
    DocumentNotFoundError,
)
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.rag.embeddings import get_embeddings
from app.rag.loaders import load_pdf
from app.rag.splitters import split_documents
from app.repositories.vector_repository import VectorRepository
from app.services.event_publisher import (
    add_outbox_event,
    safe_publish_pending_events,
)

logger = logging.getLogger(__name__)


class DocumentService:
    def __init__(self, session: Session):
        self.session = session

    def create_document_record(
            self,
            filename: str,
            file_path: str,
            user_id: int,
            file_type: str = "pdf"
    ) -> Document:
        """创建文档元数据记录（状态为 pending）"""
        doc = Document(
            filename=filename,
            file_path=file_path,
            file_type=file_type,
            user_id=user_id,
            status="pending",
        )
        self.session.add(doc)
        self.session.commit()
        self.session.refresh(doc)
        return doc

    def update_status(
            self,
            doc_id: int,
            status: str,
            page_count: int | None = None,
            error_message: str | None = None,
            clear_error: bool = False,
    ) -> Document:
        """更新文档处理状态"""
        doc = self.session.get(Document, doc_id)
        if not doc:
            raise ValueError(f"文档 {doc_id} 不存在")
        doc.status = status
        doc.updated_at = datetime.utcnow()
        if page_count is not None:
            doc.page_count = page_count
        if clear_error:
            doc.error_message = None
        elif error_message is not None:
            doc.error_message = error_message
        self.session.add(doc)
        self.session.commit()
        self.session.refresh(doc)
        return doc

    def process_pdf(self, doc_id: int) -> dict:
        """完整索引流水线：加载 → 分割 → 向量化 → 原子替换旧 chunk。

        幂等性：
        - 每次成功索引都会先删除该文档的旧 chunk，再插入新 chunk；
        - 删除 + 插入 + 状态更新在同一个事务里提交；
        - 失败时回滚，旧索引保持不变（不会出现"删除成功、插入失败"的半成品）。

        注意：Embedding 是网络调用，放在数据库事务之外执行，避免长时间持有事务。
        """
        doc = self.session.get(Document, doc_id)
        if not doc:
            raise ValueError(f"文档 {doc_id} 不存在")

        try:
            doc.status = "processing"
            doc.error_message = None
            doc.updated_at = datetime.utcnow()
            self.session.add(doc)
            self.session.commit()

            # commit 后属性会过期，重新取一次，后面用的都是最新值。
            doc = self.session.get(Document, doc_id)

            # 1. 加载 → 2. 切分 → 3. 向量化（不持有 DB 事务）
            documents = load_pdf(doc.file_path)
            chunks = split_documents(documents)
            vectors = get_embeddings().embed_documents(
                [chunk.page_content for chunk in chunks]
            )

            # 4. 原子替换：删旧 chunk + 写新 chunk + 更新文档状态
            self.session.execute(
                sa_delete(DocumentChunk).where(
                    DocumentChunk.document_id == doc_id
                )
            )
            VectorRepository(self.session).add_chunks(
                chunks=chunks,
                vectors=vectors,
                document_id=doc_id,
                user_id=doc.user_id,
            )

            doc.status = "completed"
            doc.page_count = len(documents)
            doc.chunk_count = len(chunks)
            doc.error_message = None
            doc.updated_at = datetime.utcnow()
            self.session.add(doc)
            self.session.commit()

            logger.info(
                "document indexed document_id=%s pages=%s chunks=%s",
                doc_id,
                len(documents),
                len(chunks),
            )
            return {
                "page_count": len(documents),
                "chunk_count": len(chunks),
            }

        except Exception as exc:
            self.session.rollback()
            failed = self.session.get(Document, doc_id)
            if failed is not None:
                failed.status = "failed"
                failed.error_message = str(exc)[:1000]
                failed.updated_at = datetime.utcnow()
                self.session.add(failed)
                self.session.commit()
            logger.exception(
                "document indexing failed document_id=%s",
                doc_id,
            )
            raise

    def list_documents(
            self,
            user_id: int,
            skip: int = 0,
            limit: int = 20,
            include_deleted: bool = False,
    ):
        """列出用户的文档：返回当前页 + 用户文档总数。

        默认过滤软删除文档；工作台用 include_deleted=true 展示"已删除"，
        以便用户恢复。
        """
        condition = [Document.user_id == user_id]
        if not include_deleted:
            condition.append(Document.deleted_at.is_(None))

        items_statement = (
            select(Document)
            .where(*condition)
            .order_by(Document.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        items = self.session.exec(items_statement).all()

        # total 必须单独 count：不能 len(items)——items 已被 offset/limit 截成一页，
        # len 最多等于 limit，文档超过一页时会把总数报小。
        count_statement = (
            select(func.count())
            .select_from(Document)
            .where(*condition)
        )
        total = self.session.exec(count_statement).one()
        return items, total

    def get_owned_document(
            self,
            document_id: int,
            user_id: int,
            *,
            include_deleted: bool = False,
    ) -> Document:
        """取文档并校验归属；不存在和不属于当前用户统一 404。

        include_deleted 只给删除 / 恢复这类内部操作使用；公开读接口
        必须保持默认 False，已删除文档对外表现为不存在。
        """
        doc = self.session.get(Document, document_id)
        if not doc or doc.user_id != user_id:
            raise DocumentNotFoundError()
        if doc.deleted_at is not None and not include_deleted:
            raise DocumentNotFoundError()
        return doc

    def list_chunks(
            self,
            document_id: int,
            user_id: int,
            skip: int = 0,
            limit: int = 20,
    ) -> tuple[list[DocumentChunk], int]:
        """按 chunk_index 顺序分页返回片段，供文档阅读器使用。

        先校验文档归属：不存在 / 别人的文档一律 404，
        不暴露"文档存在但你没有权限"这类信息。
        """
        self.get_owned_document(document_id, user_id)

        condition = (
            DocumentChunk.document_id == document_id,
            DocumentChunk.user_id == user_id,
        )
        total = self.session.exec(
            select(func.count())
            .select_from(DocumentChunk)
            .where(*condition)
        ).one()
        items = self.session.exec(
            select(DocumentChunk)
            .where(*condition)
            .order_by(DocumentChunk.chunk_index.asc())
            .offset(skip)
            .limit(limit)
        ).all()
        return items, total

    def get_chunk(
            self,
            document_id: int,
            user_id: int,
            chunk_index: int,
    ) -> DocumentChunk:
        """按 (document_id, chunk_index) 取单个片段，用于引用原文追溯。"""
        self.get_owned_document(document_id, user_id)

        chunk = self.session.exec(
            select(DocumentChunk).where(
                DocumentChunk.document_id == document_id,
                DocumentChunk.user_id == user_id,
                DocumentChunk.chunk_index == chunk_index,
            )
        ).first()
        if chunk is None:
            raise ChunkNotFoundError()
        return chunk

    def delete_document(self, doc_id: int, user_id: int) -> Document:
        """软删除文档：保留元数据，硬删片段/向量，并在同一事务写 outbox。

        为什么片段硬删：检索 / RAG / 片段接口都直接查 document_chunks；
        如果只标记文档删除，已删除内容仍会被检索到。硬删片段最省事，
        也不需要改所有检索 SQL。文档行保留，TaskFlow 的来源引用仍可追溯。

        物理文件先保留（软删除语义）；后续可以加清理任务。
        """
        doc = self.get_owned_document(
            doc_id,
            user_id,
            include_deleted=True,
        )

        # 幂等：重复删除不重复发事件。
        if doc.deleted_at is not None:
            return doc

        if doc.status == "processing":
            raise DocumentBusyError()

        now = datetime.utcnow()
        doc.deleted_at = now
        doc.status = "deleted"
        # 片段已硬删，计数归零，避免前端展示旧数据。
        doc.chunk_count = 0
        doc.page_count = None
        doc.updated_at = now
        doc.restore_requested = False
        self.session.add(doc)

        # 片段/向量硬删；不 commit，和 outbox 一起提交。
        VectorRepository(self.session).delete_by_document(doc_id, user_id)

        add_outbox_event(
            self.session,
            event_type="document.deleted",
            aggregate_id=doc_id,
            user_id=user_id,
            payload={"deleted_at": now.isoformat()},
        )
        self.session.commit()
        safe_publish_pending_events(self.session.get_bind())
        return doc

