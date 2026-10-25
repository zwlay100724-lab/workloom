"""密码哈希 + JWT 工具。

【为什么函数要收 secret_key 参数,而不是在模块级读环境变量?】
原 taskflow 的 security.py 在 import 时直接 os.environ.get("JWT_SECRET_KEY"),
缺了就 raise——DocMind 第一版不做登录鉴权,若它 import 这个模块就会无辜崩掉。
shared 是给多个 app 用的,不能在 import 时假设每个 app 都配了 JWT。
所以这里只做"无状态的纯工具",secret / 过期时间由 app 调用时传入自己的配置。

【Token 契约】
- sub: username，TaskFlow 用它查自己的 users 表；
- uid: users.id，DocMind 用它做资源归属过滤（跨库不能查 users 表）；
- exp: 过期时间。
TaskFlow 签发，DocMind 只验签；两个服务必须配置同一个 JWT_SECRET_KEY。
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash

DEFAULT_ALGORITHM = "HS256"
DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES = 30

password_hash = PasswordHash.recommended()


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    """解码后的 token 契约。

    subject 是 username（TaskFlow 用它查用户）；
    user_id 是 users.id（DocMind 用它做数据归属过滤）。
    旧 token 没有 uid 时 user_id 为 None，DocMind 会拒绝这类 token。
    """

    subject: str
    user_id: int | None


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_hash.verify(plain_password, hashed_password)


def create_access_token(
    *,
    subject: str,
    secret_key: str,
    user_id: int | None = None,
    algorithm: str = DEFAULT_ALGORITHM,
    expires_minutes: int = DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES,
) -> str:
    """签发 JWT。

    subject 是 username（保持旧行为）；
    user_id 是整数主键，写入 uid claim，供 DocMind 使用。
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    payload: dict[str, Any] = {"sub": subject, "exp": expire}
    if user_id is not None:
        payload["uid"] = user_id
    return jwt.encode(payload, secret_key, algorithm=algorithm)


def decode_access_token_claims(
    token: str,
    *,
    secret_key: str,
    algorithm: str = DEFAULT_ALGORITHM,
) -> AccessTokenClaims | None:
    """解码并校验 JWT，成功返回完整 claims，失败返回 None（不抛异常）。

    对 claim 做类型校验：
    - sub 必须是字符串；
    - uid 允许缺失（旧 token），但存在时必须是 int，且不能是 bool
      （JSON 的 true 会被解析成 bool，而 bool 是 int 的子类）。
    """
    try:
        payload = jwt.decode(token, secret_key, algorithms=[algorithm])
    except InvalidTokenError:
        return None

    subject = payload.get("sub")
    if not isinstance(subject, str):
        return None

    uid = payload.get("uid")
    if uid is not None and (isinstance(uid, bool) or not isinstance(uid, int)):
        return None

    return AccessTokenClaims(subject=subject, user_id=uid)


def decode_access_token(
    token: str,
    *,
    secret_key: str,
    algorithm: str = DEFAULT_ALGORITHM,
) -> str | None:
    """兼容旧调用：只返回 subject，失败返回 None。

    TaskFlow 现有的用户名查库逻辑仍然可以用它；需要 uid 的调用方
    （DocMind）使用 decode_access_token_claims。
    """
    claims = decode_access_token_claims(
        token,
        secret_key=secret_key,
        algorithm=algorithm,
    )
    return claims.subject if claims is not None else None
