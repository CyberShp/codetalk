"""Wiki cache metadata — repo-level resource for cache freshness tracking.

Wiki content lives in deepwiki's wikicache directory.
This table only stores metadata to detect staleness (branch/indexed_at/wiki_type).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base


class WikiCacheMeta(Base):
    __tablename__ = "wiki_cache_meta"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    branch: Mapped[str] = mapped_column(String(255), nullable=False)
    last_indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    wiki_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="comprehensive"
    )
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="zh")
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    repository: Mapped["Repository"] = relationship()  # noqa: F821
