import aiosqlite

from app.config import settings

# DDL executed once at startup — all tables use TEXT primary keys (UUID strings)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    repo_path TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    tools TEXT DEFAULT '[]',
    requirements_doc TEXT,
    design_doc TEXT,
    progress INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS llm_configs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    api_type TEXT NOT NULL,
    base_url TEXT NOT NULL,
    api_key TEXT NOT NULL,
    model TEXT NOT NULL,
    max_tokens INTEGER DEFAULT 4096,
    temperature REAL DEFAULT 0.3,
    config_json TEXT,
    is_chat_model INTEGER DEFAULT 1,
    is_embedding_model INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


async def init_db() -> None:
    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


async def get_db():
    """FastAPI dependency — yields an open aiosqlite connection."""
    db = await aiosqlite.connect(settings.sqlite_db)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()
