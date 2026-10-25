from pydantic import BaseModel, ConfigDict, Field


class UserRegister(BaseModel):
    username: str = Field(
        min_length=3,
        max_length=50,
    )

    password: str = Field(
        min_length=8,
        max_length=128,
    )


class UserLogin(BaseModel):
    username: str
    password: str


class UserRead(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    id: int
    username: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str