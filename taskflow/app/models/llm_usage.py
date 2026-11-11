from datetime import datetime
from decimal import Decimal

from shared.db.base import Base
from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column


class LLMUsage(Base):
    __tablename__ = "llm_usage"
    __table_args__ = (
        Index("ix_llm_usage_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 可空: /ai/chat、/ai/chat/stream 这类不带会话的调用没有 conversation_id
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    model: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # 来源：taskflow / docmind；操作：chat / rag / index ...
    source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="taskflow",
        server_default="taskflow",
    )
    operation: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="chat",
        server_default="chat",
    )

    input_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    output_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    total_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    latency_ms: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    # 单次调用的估算成本(美元),百万 token 单价由 config 中的模块级变量提供
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 6),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

