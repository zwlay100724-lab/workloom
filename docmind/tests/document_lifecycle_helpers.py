"""文档生命周期测试的共享 seed。"""
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from sqlmodel import Session

USER_ID = 1


def seed_document(
        engine,
        *,
        status: str = "completed",
        version: int = 1,
) -> int:
    with Session(engine) as session:
        doc = Document(
            filename="会议纪要.pdf",
            file_path="/tmp/meeting.pdf",
            user_id=USER_ID,
            status=status,
            index_version=version,
            chunk_count=1,
        )
        session.add(doc)
        session.flush()
        session.add(DocumentChunk(
            document_id=doc.id,
            user_id=USER_ID,
            content="完成登录接口联调",
            chunk_index=0,
            page=0,
            source="会议纪要.pdf",
        ))
        session.commit()
        session.refresh(doc)
        return doc.id
