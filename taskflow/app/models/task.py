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