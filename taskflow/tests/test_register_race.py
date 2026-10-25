"""P1-13 回归：并发注册的唯一约束冲突要映射成 409。"""
import pytest
from app.core.exceptions import UsernameAlreadyExistsError
from app.models.user import User
from app.repositories import user_repository
from app.schemas.user import UserRegister
from app.services import auth_service
from sqlmodel import Session


def test_unique_violation_is_mapped_to_conflict(
    test_engine,
    monkeypatch,
):
    with Session(test_engine) as session:
        session.add(User(username="alice", hashed_password="x"))
        session.commit()

    # 模拟并发：两个请求都通过了"用户不存在"的预检查。
    monkeypatch.setattr(
        user_repository,
        "get_user_by_username",
        lambda session, username: None,
    )

    with Session(test_engine) as session:
        with pytest.raises(UsernameAlreadyExistsError):
            auth_service.register_user(
                session,
                UserRegister(
                    username="alice",
                    password="FastAPI123",
                ),
            )
