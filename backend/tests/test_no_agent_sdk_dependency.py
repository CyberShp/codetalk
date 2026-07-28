"""Dependency gates for CodeTalk's SDK-independent production runtime."""

from __future__ import annotations

import ast
import fnmatch
import os
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOTS = (REPO_ROOT / "backend" / "app", REPO_ROOT / "deployer")
IGNORED_DIRS = {
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "playwright-report",
    "test-results",
}
DEPENDENCY_FILENAMES = {
    "Pipfile",
    "Pipfile.lock",
    "package-lock.json",
    "package.json",
    "pdm.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "uv.lock",
    "yarn.lock",
}
DEPENDENCY_GLOBS = ("requirements*.txt", "constraints*.txt", "*.requirements.in")
DIRECT_DEPENDENCY_FILENAMES = {
    "Pipfile",
    "package.json",
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
}
VENDOR_DIR_NAMES = {"third_party", "vendor", "vendored", "vendorized"}

FORBIDDEN_SDK_NAMES = {
    "Claude Agent SDK": re.compile(
        r"(?i)(?<![a-z0-9])claude[-_. ]*agent[-_. ]*sdk(?![a-z0-9])"
    ),
    "OpenAI Agents SDK": re.compile(
        r"(?i)(?<![a-z0-9])openai[-_. ]*agents(?:[-_. ]*sdk)?(?![a-z0-9])"
    ),
    "Microsoft Agent Framework": re.compile(
        r"(?i)(?<![a-z0-9])(?:microsoft[-_. ]*)?agent[-_. ]*framework(?![a-z0-9])"
    ),
    "LangGraph": re.compile(
        r"(?i)(?<![a-z0-9])langgraph(?:[-_. ][a-z0-9]+)*(?![a-z0-9])"
    ),
}
FORBIDDEN_IMPORT_PREFIXES = (
    "agent_framework",
    "agents",
    "claude_agent_sdk",
    "langgraph",
    "microsoft.agent_framework",
    "microsoft_agent_framework",
    "openai.agents",
    "openai_agents",
)

FORBIDDEN_EGRESS_DEPENDENCIES = {
    "Datadog": re.compile(r"(?i)(?<![a-z0-9])(?:datadog|ddtrace)(?![a-z0-9])"),
    "New Relic": re.compile(r"(?i)(?<![a-z0-9])new[-_. ]?relic(?![a-z0-9])"),
    "OpenTelemetry": re.compile(r"(?i)(?<![a-z0-9])opentelemetry(?:[-_. ][a-z0-9]+)*(?![a-z0-9])"),
    "PostHog": re.compile(r"(?i)(?<![a-z0-9])posthog(?![a-z0-9])"),
    "Segment": re.compile(r"(?i)(?<![a-z0-9])segment[-_. ]analytics(?![a-z0-9])"),
    "Sentry": re.compile(r"(?i)(?<![a-z0-9])sentry[-_. ]sdk(?![a-z0-9])"),
    "update checker": re.compile(r"(?i)(?<![a-z0-9])(?:pyupdater|update[-_. ]checker)(?![a-z0-9])"),
}
FORBIDDEN_EGRESS_IMPORT_PREFIXES = (
    "datadog",
    "ddtrace",
    "newrelic",
    "opentelemetry",
    "posthog",
    "segment.analytics",
    "sentry_sdk",
    "update_checker",
)


def _walk_files(root: Path):
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(
            name for name in dirs if name not in IGNORED_DIRS
        )
        current_path = Path(current)
        for filename in sorted(files):
            yield current_path / filename


def _dependency_files() -> list[Path]:
    return [
        path
        for path in _walk_files(REPO_ROOT)
        if path.name in DEPENDENCY_FILENAMES
        or any(fnmatch.fnmatch(path.name, pattern) for pattern in DEPENDENCY_GLOBS)
    ]


def _direct_dependency_files() -> list[Path]:
    return [
        path
        for path in _dependency_files()
        if path.name in DIRECT_DEPENDENCY_FILENAMES
        or any(fnmatch.fnmatch(path.name, pattern) for pattern in DEPENDENCY_GLOBS)
    ]


def _production_python_files():
    for root in PRODUCTION_ROOTS:
        for path in _walk_files(root):
            relative_parts = path.relative_to(root).parts
            if path.suffix != ".py" or "tests" in relative_parts:
                continue
            if root.name == "deployer" and path.name.startswith("test_"):
                continue
            yield path


def _vendor_roots() -> list[Path]:
    roots: list[Path] = []
    for current, dirs, _files in os.walk(REPO_ROOT):
        dirs[:] = sorted(name for name in dirs if name not in IGNORED_DIRS)
        current_path = Path(current)
        for name in list(dirs):
            if name.lower() in VENDOR_DIR_NAMES:
                roots.append(current_path / name)
                dirs.remove(name)
    return roots


def _python_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _matches(text: str, patterns: dict[str, re.Pattern[str]]) -> set[str]:
    return {label for label, pattern in patterns.items() if pattern.search(text)}


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def test_dependency_manifests_and_locks_exclude_agent_sdks():
    violations: list[str] = []
    for path in _dependency_files():
        matched = _matches(path.read_text(encoding="utf-8"), FORBIDDEN_SDK_NAMES)
        if matched:
            violations.append(f"{_relative(path)}: {', '.join(sorted(matched))}")

    assert not violations, (
        "Agent SDKs must not appear in required, locked, or optional production "
        "dependencies:\n" + "\n".join(violations)
    )


def test_production_import_graph_excludes_agent_sdk_namespaces():
    violations: list[str] = []
    for path in _production_python_files():
        for imported in sorted(_python_imports(path)):
            if any(
                imported == prefix or imported.startswith(prefix + ".")
                for prefix in FORBIDDEN_IMPORT_PREFIXES
            ):
                violations.append(f"{_relative(path)} imports {imported}")

    assert not violations, (
        "Production imports must remain independent of external Agent SDKs:\n"
        + "\n".join(violations)
    )


def test_vendor_trees_do_not_embed_agent_sdks():
    violations: list[str] = []
    for root in _vendor_roots():
        for path in _walk_files(root):
            path_matches = _matches(path.as_posix(), FORBIDDEN_SDK_NAMES)
            if path_matches:
                violations.append(
                    f"{_relative(path)} path: {', '.join(sorted(path_matches))}"
                )
            if path.suffix == ".py":
                for imported in sorted(_python_imports(path)):
                    if any(
                        imported == prefix or imported.startswith(prefix + ".")
                        for prefix in FORBIDDEN_IMPORT_PREFIXES
                    ):
                        violations.append(f"{_relative(path)} imports {imported}")

    assert not violations, (
        "Vendor trees must not embed an Agent SDK outside dependency manifests:\n"
        + "\n".join(violations)
    )


def test_production_dependencies_and_imports_exclude_telemetry_update_clients():
    violations: list[str] = []
    for path in _direct_dependency_files():
        matched = _matches(
            path.read_text(encoding="utf-8"), FORBIDDEN_EGRESS_DEPENDENCIES
        )
        if matched:
            violations.append(f"{_relative(path)}: {', '.join(sorted(matched))}")
    for path in _production_python_files():
        for imported in sorted(_python_imports(path)):
            if any(
                imported == prefix or imported.startswith(prefix + ".")
                for prefix in FORBIDDEN_EGRESS_IMPORT_PREFIXES
            ):
                violations.append(f"{_relative(path)} imports {imported}")

    assert not violations, (
        "Production must not acquire telemetry or automatic-update egress "
        "clients. Deny-only environment settings remain allowed:\n"
        + "\n".join(violations)
    )
