from app.models.user import User
from sqlmodel import Session, select


def get_user_by_username(session: Session,username: str,) -> User | None:
    statement = (
        select(User)
        .where(
            User.username == username
        )
    )
    return session.exec(statement ).first()

def add_user(session: Session,user: User,) -> None:
    session.add(user)