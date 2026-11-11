import logging
from datetime import datetime

from app.core.config import settings
from app.core.exceptions import (
    AuthStateUnavailableError,
    InvalidCredentialsError,
    UsernameAlreadyExistsError,
)
from app.core.state import get_shared_redis
from app.models.llm_usage import LLMUsage
from app.models.user import User
from app.repositories import user_repository
from app.schemas.user import (
    UserLogin,
    UserRegister,
)
from app.services.outbox_publisher import add_outbox_event
from redis.exceptions import RedisError
from shared.quota import month_start_utc, set_limit, set_used
from shared.revocation import (
    get_token_version,
    is_jti_revoked,
    revoke_jti,
    set_token_version,
)
from shared.security import (
    AccessTokenClaims,
    create_access_token,
    decode_access_token_claims,
    hash_password,
    verify_password,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

logger = logging.getLogger(__name__)


def register_user(session: Session,data: UserRegister,) -> User | None:
    existing_user = (user_repository.get_user_by_username(
            session=session,
            username=data.username,
        )
    )
    if existing_user is not None:
        raise UsernameAlreadyExistsError()
    user = User(
        username=data.username,
        hashed_password=hash_password(
            data.password
        ),
    )
    user_repository.add_user(
        session=session,
        user=user,
    )
    try:
        session.commit()
    except IntegrityError as exc:
        # 先查后插存在并发窗口：两个请求都能通过上面的存在性检查。
        # users.username 的唯一约束是最终防线，冲突统一映射成 409。
        session.rollback()
        raise UsernameAlreadyExistsError() from exc

    session.refresh(user)
    return user


def authenticate_user(
    session: Session,
    data: UserLogin,
) -> User | None:
    user = (
        user_repository
        .get_user_by_username(
            session=session,
            username=data.username,
        )
    )

    if user is None:
        return None
    if user.status != "active" or user.deleted_at is not None:
        return None

    password_ok = verify_password(
        data.password,
        user.hashed_password,
    )

    if not password_ok:
        return None
    return user


def login_user(
    session: Session,
    data: UserLogin,
) -> str | None:

    user = authenticate_user(
        session=session,
        data=data,
    )

    if user is None:
        raise InvalidCredentialsError()

    # user.id 在类型上是 int | None；能走到这里说明用户来自数据库，
    # 主键一定有值。显式兜底，避免签发一个没有 uid 的 token。
    if user.id is None:
        raise InvalidCredentialsError()

    token = create_access_token(
        subject=user.username,
        user_id=user.id,
        token_version=user.token_version,
        secret_key=settings.jwt_secret_key,
    )

    # 登录是天然同步点：把版本和配额上限写进共享 Redis，
    # DocMind 不查 TaskFlow 数据库也能判断 token 是否过旧。
    try:
        redis = get_shared_redis()
        set_token_version(redis, user.id, user.token_version)
        set_limit(redis, user.id, user.monthly_token_quota)

        # Redis 可能重启丢计数；登录时从数据库重建当月已用量。
        used = session.exec(
            select(func.coalesce(func.sum(LLMUsage.total_tokens), 0))
            .where(
                LLMUsage.user_id == user.id,
                LLMUsage.created_at >= month_start_utc(),
            )
        ).one()
        set_used(redis, user.id, int(used or 0))
    except RedisError:
        logger.warning("failed to sync auth state for user_id=%s", user.id)

    return token


def get_user_from_token(
    session: Session,
    token: str,
) -> tuple[User | None, AccessTokenClaims | None]:
    """校验签名、jti 黑名单、用户版本、用户状态，返回用户和 claims。"""

    claims = decode_access_token_claims(
        token,
        secret_key=settings.jwt_secret_key,
    )
    if claims is None or claims.user_id is None:
        return None, None

    try:
        redis = get_shared_redis()
        if claims.jti and is_jti_revoked(redis, claims.jti):
            return None, None
        current_version = get_token_version(redis, claims.user_id)
        if (
            current_version is not None
            and claims.token_version < current_version
        ):
            return None, None
    except RedisError:
        if settings.auth_fail_closed:
            raise AuthStateUnavailableError()
        logger.warning("auth state redis unavailable; failing open")

    user = session.get(User, claims.user_id)
    if user is None or user.status != "active" or user.deleted_at is not None:
        return None, None
    if claims.token_version != user.token_version:
        return None, None

    return user, claims


def logout(claims: AccessTokenClaims) -> None:
    """只吊销当前 token；其他设备不受影响。"""
    if not claims.jti or not claims.expires_at:
        return
    try:
        revoke_jti(
            get_shared_redis(),
            claims.jti,
            claims.expires_at,
        )
    except RedisError:
        logger.warning("failed to revoke jti=%s", claims.jti)


def request_account_deletion(
    session: Session,
    *,
    user: User,
    password: str,
    claims: AccessTokenClaims,
) -> None:
    """进入注销中；DocMind 数据清理完成后由 worker 硬删用户。"""
    if not verify_password(password, user.hashed_password):
        raise InvalidCredentialsError()

    # 幂等：重复注销直接返回。
    if user.status != "active":
        return
    if user.id is None:
        raise InvalidCredentialsError()

    now = datetime.utcnow()
    user.status = "deleting"
    user.deleted_at = now
    user.token_version += 1
    session.add(user)

    add_outbox_event(
        session,
        event_type="user.deleted",
        aggregate_id=user.id,
        user_id=user.id,
        payload={"requested_at": now.isoformat()},
    )
    session.commit()

    # 立即吊销：版本号让所有旧 token 失效，当前 jti 立刻进黑名单。
    try:
        redis = get_shared_redis()
        set_token_version(redis, user.id, user.token_version)
        if claims.jti and claims.expires_at:
            revoke_jti(redis, claims.jti, claims.expires_at)
    except RedisError:
        logger.warning(
            "failed to sync revocation state for user_id=%s",
            user.id,
        )
