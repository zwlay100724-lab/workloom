#!/usr/bin/env python3
"""把 eval/sample_corpus.txt 索引到 DocMind，供 evaluate.py 使用。

用法（在 docmind/ 下）：
    ../.venv/bin/python eval/index_sample.py

注意：
- 会真实调用 Embedding API，产生少量费用；
- 如果同名的已完成文档已经存在，直接跳过；
- 这是演示/评测用的最小语料，不替代真实业务文档。
"""
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

DOCMIND_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(DOCMIND_ROOT / ".env")
sys.path.insert(0, str(DOCMIND_ROOT))

from app.models.document import Document  # noqa: E402
from app.services.document_service import DocumentService  # noqa: E402
from shared.db.database import engine  # noqa: E402
from shared.logging import configure_logging  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

configure_logging()

CORPUS_PATH = Path(__file__).resolve().parent / "sample_corpus.txt"
UPLOAD_DIR = DOCMIND_ROOT / "data"
USER_ID = 1


def main() -> None:
    if not CORPUS_PATH.exists():
        raise SystemExit(f"找不到语料文件: {CORPUS_PATH}")

    with Session(engine) as session:
        existing = session.exec(
            select(Document).where(
                Document.user_id == USER_ID,
                Document.filename == CORPUS_PATH.name,
            )
        ).first()

        if existing is not None:
            print(
                f"已存在文档 id={existing.id} "
                f"status={existing.status}，跳过索引"
            )
            return

        # 复制到 data/ 再索引：删除文档时只删副本，
        # 不会把仓库里的 eval/sample_corpus.txt 一起删掉。
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        stored_path = UPLOAD_DIR / CORPUS_PATH.name
        shutil.copy2(CORPUS_PATH, stored_path)

        service = DocumentService(session)
        doc = service.create_document_record(
            filename=CORPUS_PATH.name,
            file_path=str(stored_path),
            user_id=USER_ID,
            file_type="text",
        )
        result = service.process_pdf(doc.id)
        print(f"索引完成: id={doc.id}, {result}")


if __name__ == "__main__":
    main()
