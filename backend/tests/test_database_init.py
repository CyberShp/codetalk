"""Layer 0 tests: schema creation, migration idempotency, and crash recovery."""

import re
import json
from unittest.mock import patch

import aiosqlite
import pytest

from app.database import _MIGRATIONS, _SCHEMA, init_db

_PRE_39_SCHEMA = re.sub(
    r"embedding_model_id TEXT,\n\s*", "", _SCHEMA
)


@pytest.fixture
async def fresh_db(tmp_path):
    """Bare database — schema NOT applied yet."""
    yield str(tmp_path / "fresh.db")


@pytest.fixture
async def seeded_db(tmp_path):
    """Database with schema already applied."""
    db_path = str(tmp_path / "seeded.db")
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_SCHEMA)
        await db.commit()
    yield db_path


@pytest.fixture
async def legacy_db(tmp_path):
    """Pre-#39 database — material_chunks has no embedding_model_id column."""
    db_path = str(tmp_path / "legacy.db")
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_PRE_39_SCHEMA)
        await db.commit()
    yield db_path


# ---------------------------------------------------------------------------
# Schema idempotency
# ---------------------------------------------------------------------------


class TestSchemaIdempotency:
    @pytest.mark.asyncio
    async def test_schema_applied_twice_no_error(self, tmp_path):
        db_path = str(tmp_path / "idem.db")
        async with aiosqlite.connect(db_path) as db:
            await db.executescript(_SCHEMA)
            await db.executescript(_SCHEMA)
            await db.commit()

    @pytest.mark.asyncio
    async def test_all_v2_tables_exist(self, seeded_db):
        expected = {
            "workspaces",
            "workspace_materials",
            "workspace_reports",
            "workspace_chats",
            "material_chunks",
            "ai_conversations",
            "ai_messages",
            "ai_conversation_runs",
            "ai_run_events",
        }
        async with aiosqlite.connect(seeded_db) as db:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ) as cur:
                tables = {row[0] for row in await cur.fetchall()}
        assert expected.issubset(tables)

    @pytest.mark.asyncio
    async def test_all_indexes_exist(self, seeded_db):
        expected_indexes = {
            "idx_workspace_materials_ws",
            "idx_workspace_reports_ws",
            "idx_workspace_chats_ws",
            "idx_material_chunks_ws",
            "idx_material_chunks_mat",
            "idx_ai_conversations_scope",
            "idx_ai_messages_conversation",
            "idx_ai_runs_conversation",
            "idx_ai_run_events_stream",
        }
        async with aiosqlite.connect(seeded_db) as db:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ) as cur:
                indexes = {row[0] for row in await cur.fetchall()}
        assert expected_indexes.issubset(indexes)


# ---------------------------------------------------------------------------
# Migration idempotency
# ---------------------------------------------------------------------------


class TestMigrationIdempotency:
    @pytest.mark.asyncio
    async def test_migrations_run_twice_no_error(self, seeded_db):
        async with aiosqlite.connect(seeded_db) as db:
            for _ in range(2):
                for stmt in _MIGRATIONS:
                    try:
                        await db.execute(stmt)
                    except aiosqlite.OperationalError as exc:
                        if "duplicate column" not in str(exc).lower():
                            raise
            await db.commit()

    @pytest.mark.asyncio
    async def test_embedding_model_id_column_exists_after_migration(self, seeded_db):
        async with aiosqlite.connect(seeded_db) as db:
            for stmt in _MIGRATIONS:
                try:
                    await db.execute(stmt)
                except aiosqlite.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
            await db.commit()
            async with db.execute("PRAGMA table_info(material_chunks)") as cur:
                columns = {row[1] for row in await cur.fetchall()}
        assert "embedding_model_id" in columns


# ---------------------------------------------------------------------------
# Default agent runtime seed
# ---------------------------------------------------------------------------


class TestDefaultAgentRuntimes:
    @pytest.mark.asyncio
    async def test_init_db_seeds_managed_agent_runtimes(self, fresh_db):
        with patch("app.config.settings.sqlite_db", fresh_db), \
             patch("app.api.prompts.seed_default_template", return_value=None):
            await init_db()

        async with aiosqlite.connect(fresh_db) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id, name, command, args_json, prompt_transport, output_mode, "
                "completion_mode, session_persistence, resume_args_json, timeout_seconds "
                "FROM agent_runtimes ORDER BY id"
            ) as cur:
                rows = {row["id"]: row for row in await cur.fetchall()}

        assert rows["default-claude-code"]["name"] == "Claude Code"
        assert rows["default-claude-code"]["command"] == "claude"
        assert json.loads(rows["default-claude-code"]["args_json"]) == []
        assert rows["default-claude-code"]["prompt_transport"] == "claude_print_arg"
        assert rows["default-claude-code"]["output_mode"] == "stream_json"
        assert rows["default-claude-code"]["completion_mode"] == "process_exit"
        assert rows["default-claude-code"]["session_persistence"] == "resume_args"
        assert json.loads(rows["default-claude-code"]["resume_args_json"]) == []
        assert rows["default-claude-code"]["timeout_seconds"] == 900

        assert rows["default-codex"]["name"] == "Codex"
        assert rows["default-codex"]["prompt_transport"] == "codex_exec_json"
        assert rows["default-codex"]["output_mode"] == "stream_json"
        assert rows["default-codex"]["session_persistence"] == "resume_args"

        assert rows["default-opencode"]["name"] == "OpenCode"
        assert rows["default-opencode"]["prompt_transport"] == "opencode_run_arg"
        assert rows["default-opencode"]["output_mode"] == "auto"
        assert rows["default-opencode"]["session_persistence"] == "resume_args"
        assert json.loads(rows["default-opencode"]["resume_args_json"]) == []

    @pytest.mark.asyncio
    async def test_init_db_migrates_legacy_claude_runtime_to_managed_defaults(self, fresh_db):
        async with aiosqlite.connect(fresh_db) as db:
            await db.executescript(_SCHEMA)
            await db.execute(
                """
                INSERT INTO agent_runtimes
                    (id, name, command, args_json, prompt_transport, output_mode,
                     working_dir_mode, timeout_seconds, completion_mode, idle_complete_seconds,
                     session_persistence, resume_args_json, enabled, created_at, updated_at)
                VALUES
                    ('legacy-claude', 'Claude Code', 'claude', '["code"]', 'stdin', 'plain',
                     'project', 120, 'idle_after_output', 5, 'none', '[]', 1,
                     '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
                """
            )
            await db.commit()

        with patch("app.config.settings.sqlite_db", fresh_db), \
             patch("app.api.prompts.seed_default_template", return_value=None):
            await init_db()

        async with aiosqlite.connect(fresh_db) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT command, args_json, prompt_transport, output_mode, completion_mode, "
                "session_persistence, resume_args_json, timeout_seconds "
                "FROM agent_runtimes WHERE id = 'legacy-claude'"
            ) as cur:
                row = await cur.fetchone()

        assert row is not None
        assert row["command"] == "claude"
        assert json.loads(row["args_json"]) == []
        assert row["prompt_transport"] == "claude_print_arg"
        assert row["output_mode"] == "stream_json"
        assert row["completion_mode"] == "process_exit"
        assert row["session_persistence"] == "resume_args"
        assert json.loads(row["resume_args_json"]) == []
        assert row["timeout_seconds"] == 900

    @pytest.mark.asyncio
    async def test_init_db_quarantines_ephemeral_e2e_agent_runtimes(self, fresh_db):
        async with aiosqlite.connect(fresh_db) as db:
            await db.executescript(_SCHEMA)
            await db.executemany(
                """
                INSERT INTO agent_runtimes
                    (id, name, command, args_json, prompt_transport, output_mode,
                     working_dir_mode, timeout_seconds, completion_mode, idle_complete_seconds,
                     session_persistence, resume_args_json, enabled, created_at, updated_at)
                VALUES
                    (?, ?, ?, ?, 'stdin', 'plain', 'project', 120, 'process_exit', 5,
                     'none', '[]', 1, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
                """,
                [
                    (
                        "agent-e2e-thin",
                        "E2E Thin Repair Agent",
                        "python3",
                        json.dumps(["/tmp/codetalk-agent-e2e/thin_agent.py"]),
                    ),
                    (
                        "agent-ui-probe",
                        "ui-agent-probe-failure-123",
                        "python3",
                        json.dumps(["/tmp/codetalk-agent-probe-abc/probe.py"]),
                    ),
                    (
                        "agent-real-custom",
                        "Team Claude Router",
                        "ccr",
                        json.dumps(["code"]),
                    ),
                ],
            )
            await db.commit()

        with patch("app.config.settings.sqlite_db", fresh_db), \
             patch("app.api.prompts.seed_default_template", return_value=None):
            await init_db()

        async with aiosqlite.connect(fresh_db) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT id, enabled FROM agent_runtimes") as cur:
                rows = {row["id"]: row["enabled"] for row in await cur.fetchall()}

        assert rows["agent-e2e-thin"] == 0
        assert rows["agent-ui-probe"] == 0
        assert rows["agent-real-custom"] == 1
        assert rows["default-claude-code"] == 1


# ---------------------------------------------------------------------------
# Legacy DB upgrade (real pre-#39 → current)
# ---------------------------------------------------------------------------


class TestLegacyUpgrade:
    @pytest.mark.asyncio
    async def test_legacy_db_missing_embedding_model_id(self, legacy_db):
        """Verify the legacy fixture actually lacks the column."""
        async with aiosqlite.connect(legacy_db) as db:
            async with db.execute("PRAGMA table_info(material_chunks)") as cur:
                columns = {row[1] for row in await cur.fetchall()}
        assert "embedding_model_id" not in columns

    @pytest.mark.asyncio
    async def test_init_db_adds_embedding_model_id_to_legacy(self, legacy_db):
        """Running init_db on a pre-#39 DB must add embedding_model_id via migration."""
        with patch("app.config.settings.sqlite_db", legacy_db), \
             patch("app.api.prompts.seed_default_template", return_value=None):
            await init_db()

        async with aiosqlite.connect(legacy_db) as db:
            async with db.execute("PRAGMA table_info(material_chunks)") as cur:
                columns = {row[1] for row in await cur.fetchall()}
        assert "embedding_model_id" in columns

    @pytest.mark.asyncio
    async def test_legacy_chunks_survive_upgrade(self, legacy_db):
        """Pre-existing chunks must still be queryable after migration."""
        async with aiosqlite.connect(legacy_db) as db:
            await db.execute(
                "INSERT INTO material_chunks (id, material_id, workspace_id, "
                "chunk_index, content, embedding, token_count) "
                "VALUES ('c1', 'm1', 'ws1', 0, 'test content', X'00000000', 10)"
            )
            await db.commit()

        with patch("app.config.settings.sqlite_db", legacy_db), \
             patch("app.api.prompts.seed_default_template", return_value=None):
            await init_db()

        async with aiosqlite.connect(legacy_db) as db:
            async with db.execute(
                "SELECT id, embedding_model_id FROM material_chunks WHERE id = 'c1'"
            ) as cur:
                row = await cur.fetchone()
        assert row is not None
        assert row[0] == "c1"
        assert row[1] is None


# ---------------------------------------------------------------------------
# Crash recovery resets
# ---------------------------------------------------------------------------


class TestCrashRecovery:
    @pytest.mark.asyncio
    async def test_workspace_indexed_zero_reset_to_negative_one(self, fresh_db):
        async with aiosqlite.connect(fresh_db) as db:
            await db.executescript(_SCHEMA)
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed) "
                "VALUES ('ws1', 'test', '/repo', 0)"
            )
            await db.commit()

        with patch("app.config.settings.sqlite_db", fresh_db), \
             patch("app.api.prompts.seed_default_template", return_value=None):
            await init_db()

        async with aiosqlite.connect(fresh_db) as db:
            async with db.execute(
                "SELECT indexed FROM workspaces WHERE id = 'ws1'"
            ) as cur:
                row = await cur.fetchone()
        assert row[0] == -1

    @pytest.mark.asyncio
    async def test_workspace_analyze_running_reset_to_failed(self, fresh_db):
        async with aiosqlite.connect(fresh_db) as db:
            await db.executescript(_SCHEMA)
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, analyze_status) "
                "VALUES ('ws2', 'test', '/repo', 'running')"
            )
            await db.commit()

        with patch("app.config.settings.sqlite_db", fresh_db), \
             patch("app.api.prompts.seed_default_template", return_value=None):
            await init_db()

        async with aiosqlite.connect(fresh_db) as db:
            async with db.execute(
                "SELECT analyze_status FROM workspaces WHERE id = 'ws2'"
            ) as cur:
                row = await cur.fetchone()
        assert row[0] == "failed"

    @pytest.mark.asyncio
    async def test_indexed_workspace_not_touched(self, fresh_db):
        async with aiosqlite.connect(fresh_db) as db:
            await db.executescript(_SCHEMA)
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed) "
                "VALUES ('ws3', 'indexed', '/repo', 1)"
            )
            await db.commit()

        with patch("app.config.settings.sqlite_db", fresh_db), \
             patch("app.api.prompts.seed_default_template", return_value=None):
            await init_db()

        async with aiosqlite.connect(fresh_db) as db:
            async with db.execute(
                "SELECT indexed FROM workspaces WHERE id = 'ws3'"
            ) as cur:
                row = await cur.fetchone()
        assert row[0] == 1

    @pytest.mark.asyncio
    async def test_task_running_reset_to_failed_on_restart(self, fresh_db):
        async with aiosqlite.connect(fresh_db) as db:
            await db.executescript(_SCHEMA)
            await db.execute(
                "INSERT INTO tasks (id, name, repo_path, status) "
                "VALUES ('t1', 'stuck task', '/repo', 'running')"
            )
            await db.commit()

        with patch("app.config.settings.sqlite_db", fresh_db), \
             patch("app.api.prompts.seed_default_template", return_value=None):
            await init_db()

        async with aiosqlite.connect(fresh_db) as db:
            async with db.execute(
                "SELECT status, error_message FROM tasks WHERE id = 't1'"
            ) as cur:
                row = await cur.fetchone()
        assert row[0] == "failed"
        assert row[1] == "Backend restart — task abandoned"

    @pytest.mark.asyncio
    async def test_task_pending_not_touched_on_restart(self, fresh_db):
        """User-created pending tasks (never started) must survive a restart."""
        async with aiosqlite.connect(fresh_db) as db:
            await db.executescript(_SCHEMA)
            await db.execute(
                "INSERT INTO tasks (id, name, repo_path, status) "
                "VALUES ('t2', 'queued task', '/repo', 'pending')"
            )
            await db.commit()

        with patch("app.config.settings.sqlite_db", fresh_db), \
             patch("app.api.prompts.seed_default_template", return_value=None):
            await init_db()

        async with aiosqlite.connect(fresh_db) as db:
            async with db.execute(
                "SELECT status, error_message FROM tasks WHERE id = 't2'"
            ) as cur:
                row = await cur.fetchone()
        assert row[0] == "pending"
        assert row[1] is None

    @pytest.mark.asyncio
    async def test_task_completed_not_touched_on_restart(self, fresh_db):
        async with aiosqlite.connect(fresh_db) as db:
            await db.executescript(_SCHEMA)
            await db.execute(
                "INSERT INTO tasks (id, name, repo_path, status) "
                "VALUES ('t3', 'done task', '/repo', 'completed')"
            )
            await db.commit()

        with patch("app.config.settings.sqlite_db", fresh_db), \
             patch("app.api.prompts.seed_default_template", return_value=None):
            await init_db()

        async with aiosqlite.connect(fresh_db) as db:
            async with db.execute(
                "SELECT status FROM tasks WHERE id = 't3'"
            ) as cur:
                row = await cur.fetchone()
        assert row[0] == "completed"


# ---------------------------------------------------------------------------
# init_db full run
# ---------------------------------------------------------------------------


class TestInitDbFull:
    @pytest.mark.asyncio
    async def test_init_db_on_fresh_database(self, fresh_db):
        with patch("app.config.settings.sqlite_db", fresh_db), \
             patch("app.api.prompts.seed_default_template", return_value=None):
            await init_db()

        async with aiosqlite.connect(fresh_db) as db:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ) as cur:
                tables = {row[0] for row in await cur.fetchall()}

        assert "workspaces" in tables
        assert "material_chunks" in tables

    @pytest.mark.asyncio
    async def test_init_db_idempotent(self, fresh_db):
        with patch("app.config.settings.sqlite_db", fresh_db), \
             patch("app.api.prompts.seed_default_template", return_value=None):
            await init_db()
            await init_db()

        async with aiosqlite.connect(fresh_db) as db:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ) as cur:
                tables = {row[0] for row in await cur.fetchall()}
        assert "workspaces" in tables
