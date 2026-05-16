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
    analysis_focus TEXT,
    prompt_content TEXT,
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

CREATE TABLE IF NOT EXISTS prompt_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    is_system INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coverage_analyses (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_type TEXT DEFAULT 'upload',
    status TEXT DEFAULT 'parsed',
    overall_line_rate REAL DEFAULT 0,
    overall_branch_rate REAL DEFAULT 0,
    overall_function_rate REAL DEFAULT 0,
    module_count INTEGER DEFAULT 0,
    modules_json TEXT,
    analysis_results_json TEXT,
    source_format TEXT DEFAULT 'unknown',
    created_at TEXT,
    updated_at TEXT
);
"""

_MIGRATIONS = [
    "ALTER TABLE tasks ADD COLUMN analysis_focus TEXT",
    "ALTER TABLE tasks ADD COLUMN prompt_content TEXT",
]


async def init_db() -> None:
    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.executescript(_SCHEMA)

        for stmt in _MIGRATIONS:
            try:
                await db.execute(stmt)
            except aiosqlite.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise

        await db.commit()

    from app.api.prompts import seed_default_template

    async with aiosqlite.connect(settings.sqlite_db) as db:
        db.row_factory = aiosqlite.Row
        await seed_default_template(db)


async def get_db():
    """FastAPI dependency — yields an open aiosqlite connection."""
    db = await aiosqlite.connect(settings.sqlite_db)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()
