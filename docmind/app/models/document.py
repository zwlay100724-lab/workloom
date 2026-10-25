"""文档元数据模型。

【为什么继承 SQLModel 而不是 shared 的 Base?】
docmind 里用 SQLModel 风格写的表直接继承 ``SQLModel``(和 taskflow 的 Task / User 一致)。
``table=True`` 这套"把 Field 转成 Column"的逻辑只挂在 SQLModel 的 metaclass 上;
shared 的 Base 是纯 SQLAlchemy 的 DeclarativeBase,只给 Mapped[] 风格的模型用,
拿它 + table=True 当 SQLModel 表会在 import 时直接抛 MappedAnnotationError。

两边 metadata 是同一份(shared.Base.metadata 指向 SQLModel.metadata),
所以 main.py 的 create_all / alembic autogenerate 都能看到这张表。
"""
from datetime import datetime

from sqlmodel import Field, SQLModel


class Document(SQLModel, table=True):
    """文档元数据表：记录用户上传的文档信息"""

    __tablename__ = "documents"

    id: int | None = Field(default=None, primary_key=True)
    filename: str = Field(index=True)  # 原始文件名
    file_path: str  # 服务器存储路径
    file_type: str = Field(default="pdf")  # 文件类型
    # 注意：不能写 foreign_key="users.id"——users 表属于 taskflow 自己的库(5432)，
    # docmind 是另一个库(5433)，跨库没有外键。这里只存"taskflow 用户 id"的整数，
    # 用户隔离在应用层做（每个查询都按 user_id 过滤）。
    user_id: int = Field(index=True)
    status: str = Field(default="pending")  # pending/processing/completed/failed
    page_count: int | None = Field(default=None)  # 页数（加载后填充）
    chunk_count: int | None = Field(default=None)  # 片段数（板块三后填充）
    error_message: str | None = Field(default=None)  # 处理失败时的错误信息
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)