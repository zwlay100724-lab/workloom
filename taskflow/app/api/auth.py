from fastapi import (
    APIRouter,
    status,
)

from app.api.deps import SessionDep
from app.schemas.user import (
    TokenResponse,
    UserLogin,
    UserRead,
    UserRegister,
)
from app.services import auth_service

# 这里继续使用你原项目的 engine

router = APIRouter(tags=["auth"])

@router.post(
    "/register",
    response_model=UserRead,
    status_code=(
        status.HTTP_201_CREATED
    ),
)
def register(data: UserRegister, session: SessionDep,):
    return auth_service.register_user(
        session=session,
        data=data,
    )


@router.post("/login",response_model=TokenResponse,)
def login(data: UserLogin,session: SessionDep,):
        token = (auth_service.login_user(session=session,data=data,))
        return {
            "access_token": token,
            "token_type": "bearer",
        }