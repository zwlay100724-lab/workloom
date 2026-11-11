from fastapi import (
    APIRouter,
    status,
)

from app.api.deps import (
    CurrentUserDep,
    SessionDep,
    TokenClaimsDep,
)
from app.schemas.user import (
    AccountDeleteRequest,
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


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(claims: TokenClaimsDep):
    """吊销当前 token；其他设备的 token 不受影响。"""
    auth_service.logout(claims)


@router.post("/account/delete", status_code=status.HTTP_202_ACCEPTED)
def delete_account(
    payload: AccountDeleteRequest,
    current_user: CurrentUserDep,
    claims: TokenClaimsDep,
    session: SessionDep,
):
    """请求注销：立即禁止登录并吊销 token，DocMind 清理完成后硬删用户。"""
    auth_service.request_account_deletion(
        session=session,
        user=current_user,
        password=payload.password,
        claims=claims,
    )
    return {"status": "deleting"}