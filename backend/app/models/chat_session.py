import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSON, UUID

from app.models import Base


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repo_id = Column(
        UUID(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title = Column(String(200), nullable=True)
    messages = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
