"""add processed_events for inbound TaskFlow events

Revision ID: c9e5f2a7b3d6
Revises: a8b4c1d7e2f9
Create Date: 2026-11-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9e5f2a7b3d6"
down_revision: Union[str, Sequence[str], None] = "a8b4c1d7e2f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )


def downgrade() -> None:
    op.drop_table("processed_events")
