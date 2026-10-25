import logging
import os
from datetime import datetime

from sqlalchemy import delete as sa_delete
from sqlmodel import Session, func, select

from app.core.exceptions import DocumentNotFoundError
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.rag.embeddings import get_embeddings
from app.rag.loaders import load_pdf
from app.rag.splitters import split_documents
from app.repositories.vector_repository import VectorRepository

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
            error_message: str | None = None
    ) -> Document:
        """更新文档处理状态"""
        doc = self.session.get(Document, doc_id)
        if not doc:
            raise ValueError(f"文档 {doc_id} 不存在")
        doc.status = status
        doc.updated_at = datetime.utcnow()
        if page_count is not None:
            doc.page_count = page_count
        if error_message is not None:
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

    def list_documents(self, user_id: int, skip: int = 0, limit: int = 20):
        """列出用户的文档：返回当前页 + 用户文档总数"""
        items_statement = (
            select(Document)
            .where(Document.user_id == user_id)
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
            .where(Document.user_id == user_id)
        )
        total = self.session.exec(count_statement).one()
        return items, total

    def get_owned_document(self, document_id: int, user_id: int) -> Document:
        """取文档并校验归属；不存在和不属于当前用户统一 404。"""
        doc = self.session.get(Document, document_id)
        if not doc or doc.user_id != user_id:
            raise DocumentNotFoundError()
        return doc

    def delete_document(self, doc_id: int, user_id: int) -> None:
        """删除文档：先提交数据库删除，再删物理文件。

        顺序很重要：如果先删文件、后提交数据库，commit 失败会留下"文件没了、
        记录还在"的状态。反过来，commit 成功后删文件失败只会留下孤儿文件，
        可以人工清理，不会破坏数据一致性。
        """
        doc = self.get_owned_document(doc_id, user_id)
        file_path = doc.file_path

        VectorRepository(self.session).delete_by_document(doc_id, user_id)
        self.session.delete(doc)
        self.session.commit()

        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            logger.warning(
                "failed to remove document file document_id=%s path=%s",
                doc_id,
                file_path,
                exc_info=True,
            )
