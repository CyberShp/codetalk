"""Static ownership gates for thin Provider Adapters."""

from __future__ import annotations

import ast
import re
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
SERVICES_DIR = BACKEND_ROOT / "app" / "services"
PROVIDER_ADAPTER_DIR = SERVICES_DIR / "provider_adapters"

FORBIDDEN_STORE_MODULE_PREFIXES = (
    "app.database",
    "app.services.ai_conversations",
    "app.services.ai_run_snapshots",
    "app.services.ai_thread_artifacts",
    "app.services.ai_workbench_links",
    "app.services.workbench_artifact_manifest",
    "app.services.workbench_task_run",
    "app.services.workbench_task_run_events",
    "app.services.workbench_task_store",
    "app.services.workflow_dsl",
    "app.services.workflow_version_store",
)
FORBIDDEN_STORE_SYMBOLS = {
    "AIConversationStore",
    "AIThreadSessionStore",
    "WorkbenchTaskRunEventStore",
    "WorkbenchTaskRunStore",
    "WorkbenchTaskStore",
    "WorkflowStore",
    "WorkflowVersionStore",
}
DATABASE_IMPORT_PREFIXES = (
    "aiosqlite",
    "databases",
    "peewee",
    "sqlalchemy",
    "sqlite3",
)
DATABASE_CAPABILITY_SYMBOLS = {
    "create_async_engine",
    "create_engine",
    "database_url",
    "db_path",
    "sqlite_db",
}
DATABASE_PATH_PATTERN = re.compile(r"(?i)(?:^|[/\\])[^/\\]+\.(?:db|sqlite|sqlite3)$")
FORBIDDEN_PROJECTION_FILENAMES = {"task_run.json"}
FORBIDDEN_AUTONOMOUS_EGRESS_PATTERNS = {
    "Hosted MCP endpoint": re.compile(
        r"(?i)(?:hosted|remote)[ _-]?mcp(?:[ _-]?(?:url|uri|endpoint|host))?"
    ),
    "telemetry endpoint": re.compile(
        r"(?i)(?:telemetry|tracing)[ _-]?(?:url|uri|endpoint|host)"
    ),
    "automatic update endpoint": re.compile(
        r"(?i)(?:auto(?:matic)?[ _-]?update|update[ _-]?(?:url|uri|endpoint|host))"
    ),
}


def _adapter_files() -> list[Path]:
    files = {SERVICES_DIR / "harness_facade.py"}
    files.update(PROVIDER_ADAPTER_DIR.rglob("*.py"))
    missing = sorted(path for path in files if not path.is_file())
    assert not missing, f"Provider Adapter boundary files disappeared: {missing}"
    return sorted(files)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports(tree: ast.AST) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def _string_literals(tree: ast.AST) -> set[str]:
    docstrings = _docstring_constant_ids(tree)
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    }


def _docstring_constant_ids(tree: ast.AST) -> set[int]:
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        body = node.body
        if not body or not isinstance(body[0], ast.Expr):
            continue
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            docstrings.add(id(value))
    return docstrings


def _relative(path: Path) -> str:
    return path.relative_to(BACKEND_ROOT).as_posix()


def test_provider_adapters_cannot_import_codetalk_task_or_thread_stores():
    violations: list[str] = []
    for path in _adapter_files():
        tree = _parse(path)
        for imported in sorted(_imports(tree)):
            if any(
                imported == prefix or imported.startswith(prefix + ".")
                for prefix in FORBIDDEN_STORE_MODULE_PREFIXES
            ):
                violations.append(f"{_relative(path)} imports {imported}")
        referenced = sorted(_names(tree) & FORBIDDEN_STORE_SYMBOLS)
        if referenced:
            violations.append(
                f"{_relative(path)} references {', '.join(referenced)}"
            )

    assert not violations, (
        "Provider Adapters may return provider session metadata, but only the "
        "orchestrator owns CodeTalk Task/Event/AI-thread state:\n"
        + "\n".join(violations)
    )


def test_provider_adapters_do_not_access_task_run_projection():
    violations: list[str] = []
    for path in _adapter_files():
        projections = sorted(
            value
            for value in _string_literals(_parse(path))
            if Path(value).name in FORBIDDEN_PROJECTION_FILENAMES
        )
        if projections:
            violations.append(
                f"{_relative(path)} references {', '.join(projections)}"
            )

    assert not violations, (
        "task_run.json is an orchestrator-owned projection and must be invisible "
        "to Provider Adapters:\n" + "\n".join(violations)
    )


def test_provider_adapters_cannot_create_a_second_database():
    violations: list[str] = []
    for path in _adapter_files():
        tree = _parse(path)
        database_imports = sorted(
            imported
            for imported in _imports(tree)
            if any(
                imported == prefix or imported.startswith(prefix + ".")
                for prefix in DATABASE_IMPORT_PREFIXES
            )
        )
        database_paths = sorted(
            value
            for value in _string_literals(tree)
            if DATABASE_PATH_PATTERN.search(value.strip())
        )
        database_symbols = sorted(_names(tree) & DATABASE_CAPABILITY_SYMBOLS)
        if database_imports:
            violations.append(
                f"{_relative(path)} imports {', '.join(database_imports)}"
            )
        if database_paths:
            violations.append(
                f"{_relative(path)} declares {', '.join(database_paths)}"
            )
        if database_symbols:
            violations.append(
                f"{_relative(path)} references {', '.join(database_symbols)}"
            )

    assert not violations, (
        "Provider Adapters must use the existing orchestrator-owned state and "
        "must not create a database:\n" + "\n".join(violations)
    )


def test_provider_adapters_define_no_hosted_mcp_telemetry_or_update_egress():
    violations: list[str] = []
    for path in _adapter_files():
        tree = _parse(path)
        values = _string_literals(tree) | _names(tree)
        matched = {
            label
            for label, pattern in FORBIDDEN_AUTONOMOUS_EGRESS_PATTERNS.items()
            if any(pattern.search(value) for value in values)
        }
        if matched:
            violations.append(f"{_relative(path)}: {', '.join(sorted(matched))}")

    assert not violations, (
        "Provider Adapters may contact only the configured model/CLI boundary; "
        "Hosted MCP, telemetry, and update egress must not be wired here:\n"
        + "\n".join(violations)
    )
