import json

import aiosqlite

from app.config import settings


class _UnavailableAsyncSession:
    """Compatibility shim for legacy SQLAlchemy routes in the lightweight app."""

    async def __aenter__(self):
        raise RuntimeError(
            "SQLAlchemy async_session is not configured in the lightweight "
            "SQLite runtime. Use FastAPI get_db() overrides in legacy route "
            "tests or migrate the route to aiosqlite before enabling it."
        )

    async def __aexit__(self, exc_type, exc, tb):
        return False


def async_session():
    return _UnavailableAsyncSession()

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

CREATE TABLE IF NOT EXISTS agent_runtimes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    command TEXT NOT NULL,
    args_json TEXT DEFAULT '[]',
    prompt_transport TEXT NOT NULL DEFAULT 'stdin',
    output_mode TEXT NOT NULL DEFAULT 'plain',
    working_dir_mode TEXT NOT NULL DEFAULT 'project',
    fixed_working_dir TEXT DEFAULT '',
    env_json TEXT DEFAULT '{}',
    health_command TEXT DEFAULT '',
    timeout_seconds INTEGER DEFAULT 120,
    completion_mode TEXT NOT NULL DEFAULT 'process_exit',
    idle_complete_seconds INTEGER DEFAULT 5,
    sentinel_text TEXT DEFAULT '',
    session_persistence TEXT NOT NULL DEFAULT 'none',
    resume_args_json TEXT DEFAULT '[]',
    enabled INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coverage_analyses (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_type TEXT DEFAULT 'upload',
    status TEXT DEFAULT 'parsed',
    workspace_id TEXT,
    repo_path TEXT,
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
    analyze_status TEXT,
    analyze_progress INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workspace_materials (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    content_type TEXT DEFAULT 'other',
    file_path TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
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

CREATE TABLE IF NOT EXISTS material_chunks (
    id TEXT PRIMARY KEY,
    material_id TEXT NOT NULL REFERENCES workspace_materials(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    embedding_model_id TEXT,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding BLOB NOT NULL,
    token_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ai_conversations (
    id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT 'global',
    memory_namespace TEXT NOT NULL DEFAULT 'global',
    runtime_type TEXT NOT NULL DEFAULT 'builtin_llm',
    agent_runtime_id TEXT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'idle',
    initial_context_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE,
    run_id TEXT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    references_json TEXT DEFAULT '[]',
    actions_json TEXT DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_conversation_runs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued',
    cursor INTEGER DEFAULT 0,
    error TEXT,
    model TEXT,
    token_usage_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS ai_run_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES ai_conversation_runs(id) ON DELETE CASCADE,
    conversation_id TEXT NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_agent_runtime_sessions (
    conversation_id TEXT NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE,
    agent_runtime_id TEXT NOT NULL REFERENCES agent_runtimes(id) ON DELETE CASCADE,
    cli_session_id TEXT NOT NULL,
    resume_session_id TEXT NOT NULL,
    metadata_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (conversation_id, agent_runtime_id)
);

CREATE INDEX IF NOT EXISTS idx_workspace_materials_ws ON workspace_materials(workspace_id);
CREATE INDEX IF NOT EXISTS idx_workspace_reports_ws ON workspace_reports(workspace_id);
CREATE INDEX IF NOT EXISTS idx_workspace_chats_ws ON workspace_chats(workspace_id);
CREATE INDEX IF NOT EXISTS idx_material_chunks_ws ON material_chunks(workspace_id);
CREATE INDEX IF NOT EXISTS idx_material_chunks_mat ON material_chunks(material_id);
CREATE INDEX IF NOT EXISTS idx_agent_runtimes_enabled ON agent_runtimes(enabled, updated_at);
CREATE INDEX IF NOT EXISTS idx_ai_conversations_scope ON ai_conversations(scope_type, scope_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation ON ai_messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_runs_conversation ON ai_conversation_runs(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_run_events_stream ON ai_run_events(conversation_id, event_id);
CREATE INDEX IF NOT EXISTS idx_ai_agent_runtime_sessions_runtime ON ai_agent_runtime_sessions(agent_runtime_id, updated_at);

"""

_MIGRATIONS = [
    "ALTER TABLE tasks ADD COLUMN analysis_focus TEXT",
    "ALTER TABLE tasks ADD COLUMN prompt_content TEXT",
    "ALTER TABLE tasks ADD COLUMN current_step TEXT",
    "ALTER TABLE tasks ADD COLUMN material_ids TEXT",
    "ALTER TABLE workspaces ADD COLUMN analyze_status TEXT",
    "ALTER TABLE workspaces ADD COLUMN analyze_progress INTEGER DEFAULT 0",
    "ALTER TABLE workspace_materials ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE material_chunks ADD COLUMN embedding_model_id TEXT",
    "ALTER TABLE workspaces ADD COLUMN last_index_error TEXT",
    "ALTER TABLE workspaces ADD COLUMN index_progress INTEGER DEFAULT 0",
    # F-WORKSPACE-GITNEXUS-ANALYSIS-TASK-REDESIGN: persist plan & scope on the
    # shadow task so the pipeline can drive bounded fan-out.
    "ALTER TABLE tasks ADD COLUMN analysis_plan_json TEXT",
    "ALTER TABLE tasks ADD COLUMN scope_preview_json TEXT",
    "ALTER TABLE tasks ADD COLUMN report_plan_json TEXT",
    "ALTER TABLE workspaces ADD COLUMN last_analysis_plan_json TEXT",
    "ALTER TABLE workspace_reports ADD COLUMN error TEXT",
    "ALTER TABLE workspace_reports ADD COLUMN metadata_json TEXT",
    # Workspace versioning: link tasks to workspace and tag reports by task
    "ALTER TABLE tasks ADD COLUMN workspace_id TEXT",
    "ALTER TABLE workspace_reports ADD COLUMN task_id TEXT",
    "CREATE INDEX IF NOT EXISTS idx_tasks_workspace_id ON tasks(workspace_id)",
    # Coverage uploads can now be scoped to an indexed workspace/repository.
    "ALTER TABLE coverage_analyses ADD COLUMN workspace_id TEXT",
    "ALTER TABLE coverage_analyses ADD COLUMN repo_path TEXT",
    "CREATE INDEX IF NOT EXISTS idx_coverage_workspace_id ON coverage_analyses(workspace_id)",
    "ALTER TABLE ai_conversations ADD COLUMN workspace_id TEXT NOT NULL DEFAULT 'global'",
    "ALTER TABLE ai_conversations ADD COLUMN memory_namespace TEXT NOT NULL DEFAULT 'global'",
    "ALTER TABLE ai_conversations ADD COLUMN runtime_type TEXT NOT NULL DEFAULT 'builtin_llm'",
    "ALTER TABLE ai_conversations ADD COLUMN agent_runtime_id TEXT",
    "ALTER TABLE agent_runtimes ADD COLUMN completion_mode TEXT NOT NULL DEFAULT 'process_exit'",
    "ALTER TABLE agent_runtimes ADD COLUMN idle_complete_seconds INTEGER DEFAULT 5",
    "ALTER TABLE agent_runtimes ADD COLUMN sentinel_text TEXT DEFAULT ''",
    "ALTER TABLE agent_runtimes ADD COLUMN session_persistence TEXT NOT NULL DEFAULT 'none'",
    "ALTER TABLE agent_runtimes ADD COLUMN resume_args_json TEXT DEFAULT '[]'",
    "UPDATE ai_conversations SET workspace_id = scope_id WHERE workspace_id = 'global' AND scope_type = 'workspace'",
    "UPDATE ai_conversations SET workspace_id = substr(scope_id, 1, instr(scope_id, ':') - 1) WHERE workspace_id = 'global' AND scope_type = 'module' AND instr(scope_id, ':') > 1",
    "UPDATE ai_conversations SET workspace_id = COALESCE(json_extract(initial_context_json, '$.workspace_id'), workspace_id) WHERE workspace_id = 'global' AND json_valid(initial_context_json)",
    "UPDATE ai_conversations SET memory_namespace = 'workspace:' || workspace_id WHERE workspace_id != 'global' AND memory_namespace = 'global'",
    "CREATE INDEX IF NOT EXISTS idx_ai_conversations_workspace ON ai_conversations(workspace_id, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_ai_conversations_memory_namespace ON ai_conversations(memory_namespace, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_agent_runtimes_enabled ON agent_runtimes(enabled, updated_at)",
    "CREATE TABLE IF NOT EXISTS ai_agent_runtime_sessions (conversation_id TEXT NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE, agent_runtime_id TEXT NOT NULL REFERENCES agent_runtimes(id) ON DELETE CASCADE, cli_session_id TEXT NOT NULL, resume_session_id TEXT NOT NULL, metadata_json TEXT DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (conversation_id, agent_runtime_id))",
    "CREATE INDEX IF NOT EXISTS idx_ai_agent_runtime_sessions_runtime ON ai_agent_runtime_sessions(agent_runtime_id, updated_at)",
]


_DEFAULT_AGENT_RUNTIMES = [
    {
        "id": "default-claude-code",
        "name": "Claude Code",
        "command": "claude",
        "args": [],
        "prompt_transport": "claude_print_arg",
        "output_mode": "stream_json",
        "working_dir_mode": "project",
        "fixed_working_dir": "",
        "env": {},
        "health_command": "",
        "timeout_seconds": 900,
        "completion_mode": "process_exit",
        "idle_complete_seconds": 5,
        "sentinel_text": "",
        "session_persistence": "resume_args",
        "resume_args": [],
        "enabled": 1,
    },
    {
        "id": "default-codex",
        "name": "Codex",
        "command": "codex",
        "args": [],
        "prompt_transport": "codex_exec_json",
        "output_mode": "stream_json",
        "working_dir_mode": "project",
        "fixed_working_dir": "",
        "env": {},
        "health_command": "",
        "timeout_seconds": 900,
        "completion_mode": "process_exit",
        "idle_complete_seconds": 5,
        "sentinel_text": "",
        "session_persistence": "resume_args",
        "resume_args": [],
        "enabled": 1,
    },
    {
        "id": "default-opencode",
        "name": "OpenCode",
        "command": "opencode",
        "args": [],
        "prompt_transport": "opencode_run_arg",
        "output_mode": "auto",
        "working_dir_mode": "project",
        "fixed_working_dir": "",
        "env": {},
        "health_command": "",
        "timeout_seconds": 900,
        "completion_mode": "process_exit",
        "idle_complete_seconds": 5,
        "sentinel_text": "",
        "session_persistence": "resume_args",
        "resume_args": [],
        "enabled": 1,
    },
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

        # Reset workspaces stuck in background tasks from a prior crash
        await db.execute(
            "UPDATE workspaces SET indexed = -1, updated_at = CURRENT_TIMESTAMP"
            " WHERE indexed = 0"
        )
        await db.execute(
            "UPDATE workspaces SET analyze_status = 'failed', updated_at = CURRENT_TIMESTAMP"
            " WHERE analyze_status = 'running'"
        )

        # Reset tasks stuck in 'running' from a prior crash — their asyncio
        # coroutines are gone after restart. Leave 'pending' tasks untouched:
        # those are user-created drafts that were never started.
        await db.execute(
            "UPDATE tasks SET status = 'failed',"
            " error_message = 'Backend restart — task abandoned',"
            " updated_at = CURRENT_TIMESTAMP"
            " WHERE status = 'running'"
        )
        await _seed_default_agent_runtimes(db)
        await _migrate_legacy_agent_runtimes(db)
        await _quarantine_ephemeral_agent_runtimes(db)

        await db.commit()

    from app.api.prompts import seed_default_template

    async with aiosqlite.connect(settings.sqlite_db) as db:
        db.row_factory = aiosqlite.Row
        await seed_default_template(db)


async def _seed_default_agent_runtimes(db: aiosqlite.Connection) -> None:
    for runtime in _DEFAULT_AGENT_RUNTIMES:
        await db.execute(
            """
            INSERT INTO agent_runtimes
                (id, name, command, args_json, prompt_transport, output_mode,
                 working_dir_mode, fixed_working_dir, env_json, health_command,
                 timeout_seconds, completion_mode, idle_complete_seconds, sentinel_text,
                 session_persistence, resume_args_json, enabled, created_at, updated_at)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                command = excluded.command,
                args_json = excluded.args_json,
                prompt_transport = excluded.prompt_transport,
                output_mode = excluded.output_mode,
                working_dir_mode = excluded.working_dir_mode,
                fixed_working_dir = excluded.fixed_working_dir,
                env_json = excluded.env_json,
                health_command = excluded.health_command,
                timeout_seconds = excluded.timeout_seconds,
                completion_mode = excluded.completion_mode,
                idle_complete_seconds = excluded.idle_complete_seconds,
                sentinel_text = excluded.sentinel_text,
                session_persistence = excluded.session_persistence,
                resume_args_json = excluded.resume_args_json,
                enabled = excluded.enabled,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                runtime["id"],
                runtime["name"],
                runtime["command"],
                json.dumps(runtime["args"], ensure_ascii=False),
                runtime["prompt_transport"],
                runtime["output_mode"],
                runtime["working_dir_mode"],
                runtime["fixed_working_dir"],
                json.dumps(runtime["env"], ensure_ascii=False, sort_keys=True),
                runtime["health_command"],
                runtime["timeout_seconds"],
                runtime["completion_mode"],
                runtime["idle_complete_seconds"],
                runtime["sentinel_text"],
                runtime["session_persistence"],
                json.dumps(runtime["resume_args"], ensure_ascii=False),
                runtime["enabled"],
            ),
        )


async def _migrate_legacy_agent_runtimes(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        UPDATE agent_runtimes
        SET
            output_mode = 'auto',
            timeout_seconds = MAX(timeout_seconds, 900),
            completion_mode = 'process_exit',
            idle_complete_seconds = 5,
            sentinel_text = '',
            session_persistence = 'resume_args',
            resume_args_json = '[]',
            updated_at = CURRENT_TIMESTAMP
        WHERE id = 'default-opencode'
          AND prompt_transport = 'opencode_run_arg'
        """
    )
    await db.execute(
        """
        UPDATE agent_runtimes
        SET
            command = 'claude',
            args_json = '[]',
            prompt_transport = 'claude_print_arg',
            output_mode = 'stream_json',
            timeout_seconds = MAX(timeout_seconds, 900),
            completion_mode = 'process_exit',
            idle_complete_seconds = 5,
            sentinel_text = '',
            session_persistence = 'resume_args',
            resume_args_json = '[]',
            updated_at = CURRENT_TIMESTAMP
        WHERE id != 'default-claude-code'
          AND lower(name) IN ('claude code', 'claude code router')
          AND prompt_transport IN ('stdin', 'argv_last')
          AND output_mode IN ('plain', 'auto')
        """
    )


async def _quarantine_ephemeral_agent_runtimes(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        UPDATE agent_runtimes
        SET
            enabled = 0,
            updated_at = CURRENT_TIMESTAMP
        WHERE enabled = 1
          AND id NOT IN ('default-claude-code', 'default-codex', 'default-opencode')
          AND (
            lower(name) GLOB 'e2e *'
            OR lower(name) GLOB 'ui-agent-*'
            OR lower(name) GLOB '* test runtime *'
            OR args_json LIKE '%/tmp/codetalk-agent-e2e/%'
            OR args_json LIKE '%codetalk-agent-probe-%'
          )
        """
    )


async def get_db():
    """FastAPI dependency — yields an open aiosqlite connection."""
    db = await aiosqlite.connect(settings.sqlite_db)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()
