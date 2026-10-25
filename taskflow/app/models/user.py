from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    __tablename__ = "users"
    id: int | None = Field(default=None,primary_key=True,)
    username: str = Field(index=True,unique=True,max_length=50,)
    hashed_password: str = Field(max_length=255,)