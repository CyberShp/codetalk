from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Import all models so Alembic can detect them
from app.models.project import Project  # noqa: E402, F401
from app.models.repository import Repository  # noqa: E402, F401
from app.models.task import AnalysisTask, TaskLog, ToolRun  # noqa: E402, F401
from app.models.llm_config import LLMConfig  # noqa: E402, F401
