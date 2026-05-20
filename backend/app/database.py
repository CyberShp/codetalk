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
    deepwiki_depth TEXT DEFAULT 'balanced',
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

CREATE TABLE IF NOT EXISTS task_chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_task_chats_task ON task_chats(task_id);

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    repo_path TEXT NOT NULL,
    indexed INTEGER DEFAULT 0,
    index_job TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workspace_materials (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    content_type TEXT DEFAULT 'other',
    file_path TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workspace_reports (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    report_type TEXT NOT NULL,
    title TEXT,
    content TEXT,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workspace_chats (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    mode TEXT NOT NULL DEFAULT 'freeqa',
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    attachments TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_workspace_materials_ws ON workspace_materials(workspace_id);
CREATE INDEX IF NOT EXISTS idx_workspace_reports_ws ON workspace_reports(workspace_id);
CREATE INDEX IF NOT EXISTS idx_workspace_chats_ws ON workspace_chats(workspace_id);

CREATE TABLE IF NOT EXISTS deepwiki_repos (
    id TEXT PRIMARY KEY,
    repo_path TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    page_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    progress INTEGER DEFAULT 0,
    wiki_data TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_MIGRATIONS = [
    "ALTER TABLE tasks ADD COLUMN analysis_focus TEXT",
    "ALTER TABLE tasks ADD COLUMN prompt_content TEXT",
    "ALTER TABLE tasks ADD COLUMN current_step TEXT",
    "ALTER TABLE tasks ADD COLUMN deepwiki_depth TEXT DEFAULT 'balanced'",
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

        # Reset any deepwiki_repos rows stuck in 'running' from a prior crash
        await db.execute(
            "UPDATE deepwiki_repos SET status = 'failed', updated_at = CURRENT_TIMESTAMP"
            " WHERE status = 'running'"
        )

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
