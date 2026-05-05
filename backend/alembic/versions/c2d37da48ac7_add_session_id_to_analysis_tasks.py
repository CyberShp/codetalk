"""add_session_id_to_analysis_tasks

Revision ID: c2d37da48ac7
Revises: d4e5f6a7b8c9
Create Date: 2026-05-05 23:10:25.118306

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c2d37da48ac7'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('analysis_tasks', sa.Column('session_id', sa.String(length=36), nullable=True))
    op.create_index(op.f('ix_analysis_tasks_session_id'), 'analysis_tasks', ['session_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_analysis_tasks_session_id'), table_name='analysis_tasks')
    op.drop_column('analysis_tasks', 'session_id')
