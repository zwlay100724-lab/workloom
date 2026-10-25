from datetime import date

from sqlmodel import Field, SQLModel

#创建数据库模型

class Task(SQLModel, table=True):#table=True；告诉SQLModel这个类不只是普通数据模型，它应该映射成数据库表。
    id: int | None = Field(
        default=None,
        primary_key=True
    )
    title: str
    description: str | None = None
    done: bool = False
    priority: str = "medium"
    deadline: date | None = None

    owner_id: int = Field(
        foreign_key="users.id",
        index=True,
    )

    # ── 来源引用（「文档 → 任务」生成时写入）──
    # 只存 DocMind 的 document_id：两个服务数据库独立，不建跨库外键。
    # filename 是生成时的快照，展示引用时不必再回查 DocMind。
    source_document_id: int | None = Field(default=None, index=True)
    source_filename: str | None = Field(default=None)
    source_chunk_index: int | None = Field(default=None)