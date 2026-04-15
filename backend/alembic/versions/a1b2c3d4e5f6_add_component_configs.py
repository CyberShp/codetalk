"""add component_configs table

Revision ID: a1b2c3d4e5f6
Revises: e3bc470b7e4d
Create Date: 2026-04-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a1b2c3d4e5f6"
down_revision = "e3bc470b7e4d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "component_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("component", sa.String(50), nullable=False),
        sa.Column("domain", sa.String(50), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "component", "domain", name="uq_component_domain"
        ),
    )


def downgrade() -> None:
    op.drop_table("component_configs")
