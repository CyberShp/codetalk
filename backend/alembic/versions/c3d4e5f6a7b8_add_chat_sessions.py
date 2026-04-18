"""add chat sessions

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-18 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON, UUID

# revision identifiers, used by Alembic.
revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "repo_id",
            UUID(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("messages", JSON, nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime,
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("chat_sessions")
