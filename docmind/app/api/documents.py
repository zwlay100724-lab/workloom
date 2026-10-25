import uuid
from pathlib import Path
from typing import BinaryIO

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
)
from shared.db.database import get_session
from sqlmodel import Session

from app.api.deps import CurrentUserIdDep
from app.schemas.document import DocumentListResponse, DocumentResponse
from app.services.document_service import DocumentService
from app.services.indexing import index_document

router = APIRouter(prefix="/documents", tags=["文档管理"])

# 用绝对路径：uvicorn 的 cwd 可能是仓库根、docmind/ 或容器里的任意目录，
# 相对路径 "data" 会随启动方式漂移，导致上传和读取指向不同目录。
UPLOAD_DIR = Path(__file__).resolve().parents[2] / "data"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB
READ_CHUNK_SIZE = 1024 * 1024        # 1 MB，分块写盘，避免整文件进内存

# 扩展名 → 业务类型。数据库里的 file_type 取这里的值，
# 不再把 docx/md/txt 一律写成 "pdf"。
EXTENSION_TO_TYPE = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".md": "text",
    ".txt": "text",
}

# 文件头签名：PDF 以 %PDF- 开头；docx 本质是 zip，以 PK 开头。
# 只做最低限度的防伪装，不替代完整的 MIME 检测。
MAGIC_HEADERS = {
    "pdf": b"%PDF-",
    "docx": b"PK",
}

# 用户身份统一从 JWT 的 uid claim 取，见 app/api/deps.py。
# 绝不再使用固定的占位 user_id。


def sanitize_filename(filename: str) -> str:
    """只保留最终文件名，阻止 ../ 和绝对路径写出上传目录。

    UploadFile.filename 来自客户端，不能直接拼进路径：
    客户端传 "../../x" 或 "..\\..\\x" 时，os.path.join 会跳出 data/。
    这里同时处理 POSIX 和 Windows 分隔符，并去掉控制字符。
    """
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = "".join(ch for ch in name if ch.isprintable())
    if not name or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="文件名不合法")
    return name[:200]


def detect_file_type(filename: str) -> str:
    """按扩展名返回业务类型；不在白名单内直接 400。"""
    suffix = Path(filename).suffix.lower()
    file_type = EXTENSION_TO_TYPE.get(suffix)
    if file_type is None:
        raise HTTPException(
            status_code=400,
            detail=f"支持的格式: {', '.join(sorted(EXTENSION_TO_TYPE))}",
        )
    return file_type


def validate_signature(head: bytes, file_type: str) -> None:
    """检查文件头是否与扩展名匹配，拦截改后缀的伪装文件。"""
    expected = MAGIC_HEADERS.get(file_type)
    if expected is not None and not head.startswith(expected):
        raise HTTPException(status_code=400, detail="文件内容与扩展名不匹配")


def save_upload(source: BinaryIO, destination: Path) -> bytes:
    """分块写盘，返回文件头若干字节；超限/空文件时删除半成品。

    为什么不用 await file.read() 一次读完：
    大文件会完整进内存，且没有上限；分块写入可以在超限时立刻停止。
    """
    head = b""
    total = 0

    try:
        with destination.open("wb") as output:
            while True:
                chunk = source.read(READ_CHUNK_SIZE)
                if not chunk:
                    break

                if not head:
                    head = chunk[:8]

                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "文件超过 "
                            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB 限制"
                        ),
                    )
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    if total == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="文件为空")

    return head


@router.post("/upload", response_model=DocumentResponse)
def upload_document(
        background_tasks: BackgroundTasks,
        current_user_id: CurrentUserIdDep,
        file: UploadFile = File(...),
        session: Session = Depends(get_session),
):
    """上传文档，立即返回 pending，解析/切片/向量化在后台执行。

    后台任务使用 session.get_bind() 重新创建 Session：
    - 请求级 session 在响应后关闭，不能复用；
    - 测试里 dependency override 到独立测试库时，后台任务也会写同一个库。
    """
    original_name = sanitize_filename(file.filename or "")
    file_type = detect_file_type(original_name)

    # 存储名用 UUID，不复用客户端文件名：
    # 1) 彻底消除路径穿越和重名覆盖；2) 原始文件名只作为展示字段入库。
    stored_name = f"{uuid.uuid4().hex}{Path(original_name).suffix.lower()}"
    file_path = UPLOAD_DIR / stored_name

    head = save_upload(file.file, file_path)

    try:
        validate_signature(head, file_type)
    except Exception:
        file_path.unlink(missing_ok=True)
        raise

    service = DocumentService(session)
    try:
        doc = service.create_document_record(
            filename=original_name,
            file_path=str(file_path),
            user_id=current_user_id,
            file_type=file_type,
        )
    except Exception:
        # 元数据没写成功，物理文件不能留成孤儿。
        file_path.unlink(missing_ok=True)
        raise

    background_tasks.add_task(
        index_document,
        doc.id,
        session.get_bind(),
    )
    return doc


@router.get("", response_model=DocumentListResponse)
def list_documents(
        current_user_id: CurrentUserIdDep,
        skip: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
        session: Session = Depends(get_session),
):
    """获取当前用户的文档列表"""
    service = DocumentService(session)
    items, total = service.list_documents(current_user_id, skip, limit)
    return DocumentListResponse(items=items, total=total)


@router.get("/{doc_id}", response_model=DocumentResponse)
def get_document(
    doc_id: int,
    current_user_id: CurrentUserIdDep,
    session: Session = Depends(get_session),
):
    """获取单个文档状态，供前端/ smoke 轮询索引进度。"""
    return DocumentService(session).get_owned_document(
        doc_id,
        current_user_id,
    )


@router.delete("/{doc_id}")
def delete_document(
    doc_id: int,
    current_user_id: CurrentUserIdDep,
    session: Session = Depends(get_session),
):
    """删除文档及其所有向量（含物理文件）。"""
    service = DocumentService(session)
    # 不存在或不属于当前用户时，DocumentService 抛 DocumentNotFoundError，
    # 由 shared 的统一异常处理器返回 404。
    service.delete_document(doc_id, current_user_id)
    return {"message": "删除成功"}
