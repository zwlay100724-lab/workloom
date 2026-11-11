from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from shared.db.database import get_session
from shared.security import AccessTokenClaims, decode_access_token_claims
from sqlmodel import Session

from app.core.config import settings
from app.models.user import User
from app.services import auth_service

SessionDep = Annotated[Session, Depends(get_session)]

# HTTPBearer 会让 Swagger 出现 Authorize 按钮(security scheme: http/bearer),
# 并且在请求里自动带 Authorization: Bearer <token>。
# auto_error=False:缺失/错误 scheme 时不抛错,由下面手动返回 401。
bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    session: SessionDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> User:
    if credentials is None:
        raise _unauthorized()
    user, _claims = auth_service.get_user_from_token(
        session=session,
        token=credentials.credentials,
    )
    if user is None:
        raise _unauthorized("Invalid or expired token")
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
        raise _unauthorized()
    return credentials.credentials


def get_token_claims(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> AccessTokenClaims:
    """只解签名，不查库：给 logout / 注销这类需要 jti 的接口用。"""
    if credentials is None:
        raise _unauthorized()
    claims = decode_access_token_claims(
        credentials.credentials,
        secret_key=settings.jwt_secret_key,
    )
    if claims is None or claims.user_id is None:
        raise _unauthorized("Invalid or expired token")
    return claims


CurrentUserDep = Annotated[
    User,
    Depends(get_current_user),
]

AccessTokenDep = Annotated[
    str,
    Depends(get_access_token),
]

TokenClaimsDep = Annotated[
    AccessTokenClaims,
    Depends(get_token_claims),
]
