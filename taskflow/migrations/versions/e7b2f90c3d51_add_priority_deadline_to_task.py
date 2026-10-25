"""add priority and deadline to task

Revision ID: e7b2f90c3d51
Revises: dd9137b86f37
Create Date: 2026-08-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7b2f90c3d51'
down_revision: Union[str, Sequence[str], None] = 'dd9137b86f37'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 已有行需要 server_default 才能加上非空列,新值统一回填为 medium
    op.add_column(
        'task',
        sa.Column('priority', sa.String(), server_default='medium', nullable=False),
    )
    op.add_column(
        'task',
        sa.Column('deadline', sa.Date(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('task', 'deadline')
    op.drop_column('task', 'priority')