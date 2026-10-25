"""DocMind 的鉴权依赖。

TaskFlow 签发 JWT，DocMind 只验签：
- 不查 TaskFlow 的 users 表（两个服务数据库独立）；
- 从 token 的 uid claim 取用户 ID；
- 所有资源归属过滤只使用这个服务端解析出的 user_id，
  绝不相信请求体/query 里的 user_id。
"""
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from shared.security import decode_access_token_claims

from app.core.config import settings

# auto_error=False：缺失/格式不对时由下面手动返回统一的 401，
# 同时 Swagger 会出现 Authorize 按钮。
bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user_id(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> int:
    """从 Bearer token 中解析出当前用户 ID。

    旧 token 没有 uid（TaskFlow 升级前签发的）会被拒绝，
    用户重新登录一次即可。
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    claims = decode_access_token_claims(
        credentials.credentials,
        secret_key=settings.JWT_SECRET_KEY,
    )
    if claims is None or claims.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return claims.user_id


CurrentUserIdDep = Annotated[int, Depends(get_current_user_id)]
