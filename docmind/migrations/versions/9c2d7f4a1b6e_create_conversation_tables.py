"""create conversations and conversation_messages tables

Revision ID: 9c2d7f4a1b6e
Revises: b67939cd0672
Create Date: 2026-10-23

【为什么补这条迁移？】
app/models/conversation.py 里的两张表一直只存在于代码里，迁移历史中
没有它们。新环境执行 `alembic upgrade head` 后，带 conversation_id 的
/rag/query 会在 get_history() 处报 relation does not exist。

表结构严格对齐 SQLModel 模型：
- 时间字段是 Python 侧 default_factory，所以没有 server_default；
- user_id 不建外键（users 表属于 taskflow 的另一个库，跨库无 FK）；
- conversation_messages.conversation_id 建同库外键。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c2d7f4a1b6e'
down_revision: Union[str, Sequence[str], None] = 'b67939cd0672'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'conversations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=None), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_conversations_user_id'),
        'conversations',
        ['user_id'],
        unique=False,
    )

    op.create_table(
        'conversation_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=None), nullable=False),
        sa.Column('content', sa.String(length=None), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['conversation_id'],
            ['conversations.id'],
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_conversation_messages_conversation_id'),
        'conversation_messages',
        ['conversation_id'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f('ix_conversation_messages_conversation_id'),
        table_name='conversation_messages',
    )
    op.drop_table('conversation_messages')
    op.drop_index(
        op.f('ix_conversations_user_id'),
        table_name='conversations',
    )
    op.drop_table('conversations')
