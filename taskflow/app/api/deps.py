from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from shared.db.database import get_session
from sqlmodel import Session

from app.models.user import User
from app.services import auth_service

SessionDep = Annotated[Session, Depends(get_session)]

# HTTPBearer 会让 Swagger 出现 Authorize 按钮(security scheme: http/bearer),
# 并且在请求里自动带 Authorization: Bearer <token>。
# auto_error=False:缺失/错误 scheme 时不抛错,由下面手动返回 401。
bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    session: SessionDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = auth_service.get_user_from_token(
        session=session,
        token=credentials.credentials,
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_access_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> str:
    """取出原始 Bearer token。

    跨服务调用（TaskFlow → DocMind）时原样透传：DocMind 和 TaskFlow
    共用同一个 JWT secret，验签后仍能拿到同一个 uid，身份不会断。
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


CurrentUserDep = Annotated[
    User,
    Depends(get_current_user),
]

AccessTokenDep = Annotated[
    str,
    Depends(get_access_token),
]