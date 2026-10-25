"""add task_documents and task citation fields

Revision ID: a4c9e2f71b30
Revises: 179eb3da9449
Create Date: 2026-10-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4c9e2f71b30'
down_revision: Union[str, Sequence[str], None] = '179eb3da9449'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 任务上的来源引用：由「文档生成任务」写入。
    op.add_column(
        'task',
        sa.Column('source_document_id', sa.Integer(), nullable=True),
    )
    op.add_column(
        'task',
        sa.Column('source_filename', sa.String(), nullable=True),
    )
    op.add_column(
        'task',
        sa.Column('source_chunk_index', sa.Integer(), nullable=True),
    )
    op.create_index(
        op.f('ix_task_source_document_id'),
        'task',
        ['source_document_id'],
        unique=False,
    )

    # 任务 ↔ 文档关联表：只存 DocMind 的 document_id，跨库无外键。
    op.create_table(
        'task_documents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['task.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'task_id',
            'document_id',
            name='uq_task_documents_task_document',
        ),
    )
    op.create_index(
        op.f('ix_task_documents_task_id'),
        'task_documents',
        ['task_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_task_documents_document_id'),
        'task_documents',
        ['document_id'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f('ix_task_documents_document_id'),
        table_name='task_documents',
    )
    op.drop_index(
        op.f('ix_task_documents_task_id'),
        table_name='task_documents',
    )
    op.drop_table('task_documents')

    op.drop_index(
        op.f('ix_task_source_document_id'),
        table_name='task',
    )
    op.drop_column('task', 'source_chunk_index')
    op.drop_column('task', 'source_filename')
    op.drop_column('task', 'source_document_id')
