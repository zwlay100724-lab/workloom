from datetime import datetime

from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    __tablename__ = "users"
    id: int | None = Field(default=None,primary_key=True,)
    username: str = Field(index=True,unique=True,max_length=50,)
    hashed_password: str = Field(max_length=255,)

    # 注销 saga：active → deleting → deleted（完成后行被硬删）
    status: str = Field(default="active", max_length=20)
    deleted_at: datetime | None = Field(default=None)
    # 注销/改密时递增，旧 token 的 ver 小于它即失效
    token_version: int = Field(default=0)
    # 每月 token 配额；TaskFlow 登录时写入共享 Redis
    monthly_token_quota: int = Field(default=1_000_000)