"""add estimated_cost_usd to llm_usage

Revision ID: dd9137b86f37
Revises: 7b426f62c1fa
Create Date: 2026-08-23 12:22:59.228196

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dd9137b86f37'
down_revision: Union[str, Sequence[str], None] = '7b426f62c1fa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('llm_usage', sa.Column('estimated_cost_usd', sa.Numeric(precision=10, scale=6), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('llm_usage', 'estimated_cost_usd')
