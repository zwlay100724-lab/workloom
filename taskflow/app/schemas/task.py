from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


#将Pydantic 接入 FastAPI，最后创建的tasks为对象，
# 在保存数据库、更新数据时经常需要Pydantic Model 转 dict，利用task.model_dump()将model序列转化为字典
#在当前 Pydantic 中，model_dump() 是把 Model 序列化成字典的主要方法之一
#通过 Pydantic 的 Field() 给 Request Body 模型字段声明额外校验和元数据。
class TaskCreate(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )
    # 不接受客户端指定主键；id 由数据库生成。
    title: str=Field(min_length=1,max_length=50)
    done: bool=Field(default=False)
    description: str | None = Field(default=None,max_length=500)
    priority: Literal["low", "medium", "high"] = "medium"
    deadline: date | None = None
    @field_validator("title") #Pydantic 当前提供 field validators 来为具体字段添加超出普通类型/Field 约束的验证逻辑。
    @classmethod
    def validate_title(cls, value: str):
        value = value.strip()
        if not value:
            raise ValueError("title cannot be empty")
        return value
#用于创建response_model，因为客户端输入和服务端响应的数据格式有时会不一样
#FastAPI 会按照 response_model 对输出进行验证、文档生成以及过滤，只返回响应模型声明的数据结构。
class TaskResponse(BaseModel):
    # from_attributes：允许直接用 ORM Task 对象构造（生成任务接口要这么做）。
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None
    done: bool
    priority: str
    deadline: date | None
    # 来源引用：只有「文档生成任务」创建的 task 才有值。
    source_document_id: int | None = None
    source_filename: str | None = None
    source_chunk_index: int | None = None
#TaskCreate控制输入，TaskResponse控制输出

class TaskUpdate(BaseModel):
    title: str = Field(min_length=1,max_length=100 )
    description: str | None = Field(default=None, max_length=500)
    done: bool  #title和done必填
    priority: Literal["low", "medium", "high"] = "medium"
    deadline: date | None = None

class TaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100 )
    description: str | None = Field( default=None,max_length=500 )
    done: bool | None = None#现在所有的字段都可以选择不提供，不是必填的，这是与TaskUpdate的区别
    priority: Literal["low", "medium", "high"] | None = None
    deadline: date | None = None