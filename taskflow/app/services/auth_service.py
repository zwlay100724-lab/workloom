from app.core.config import settings
from app.core.exceptions import (
    InvalidCredentialsError,
    UsernameAlreadyExistsError,
)
from app.models.user import User
from app.repositories import user_repository
from app.schemas.user import (
    UserLogin,
    UserRegister,
)
from shared.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session


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

    return create_access_token(
        subject=user.username,
        user_id=user.id,
        secret_key=settings.jwt_secret_key,
    )

def get_user_from_token(
    session: Session,
    token: str,
) -> User | None:

    username = decode_access_token(
        token,
        secret_key=settings.jwt_secret_key,
    )

    if username is None:
        return None

    return (
        user_repository
        .get_user_by_username(
            session=session,
            username=username,
        )
    )