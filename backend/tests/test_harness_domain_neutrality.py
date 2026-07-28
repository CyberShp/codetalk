"""Static architecture gates for the domain-neutral Harness boundary."""

from __future__ import annotations

import ast
import re
from pathlib import Path


SERVICES_DIR = Path(__file__).resolve().parents[1] / "app" / "services"

# Compatibility code may translate a frozen pre-V3 contract for one migration
# window. The exception is intentionally filename-based so a general production
# module cannot acquire it accidentally.
LEGACY_COMPATIBILITY_FILENAMES = {
    "legacy_compatibility.py",
    "legacy_provider_adapter.py",
    "legacy_harness_adapter.py",
}

PROFESSIONAL_MODULE_PREFIXES = (
    "app.services.artifact_contract_v3",
    "app.services.behavior_claim_validator",
    "app.services.governance_plugins",
    "app.services.regular_stage_governance",
    "app.services.source_driven_test_design",
    "app.services.test_activity_contract",
    "app.services.test_activity_stage_specs",
    "app.services.test_point_generator",
    "app.services.test_semantic_library",
)

DOMAIN_PATTERNS = {
    "SFMEA": re.compile(r"(?i)(?<![a-z0-9])sfmea(?![a-z0-9])"),
    "RPN": re.compile(r"(?i)(?<![a-z0-9])rpn(?![a-z0-9])"),
    "black-box": re.compile(r"(?i)(?<![a-z0-9])black[ _-]?box(?![a-z0-9])"),
    "iSCSI": re.compile(r"(?i)(?<![a-z0-9])iscsi(?![a-z0-9])"),
    "CHAP": re.compile(r"(?i)(?<![a-z0-9])chap(?![a-z0-9])"),
    "NVMe": re.compile(r"(?i)(?<![a-z0-9])nvme(?![a-z0-9])"),
    "test-activity": re.compile(
        r"(?i)(?<![a-z0-9])test[ _-]?activity(?![a-z0-9])"
    ),
}


def _boundary_files() -> list[Path]:
    files = {
        SERVICES_DIR / "agent_cli_bridge.py",
        SERVICES_DIR / "agent_invocation_contract.py",
        SERVICES_DIR / "agent_run_harness.py",
        SERVICES_DIR / "harness_facade.py",
    }
    files.update((SERVICES_DIR / "provider_adapters").rglob("*.py"))
    missing = sorted(path for path in files if not path.is_file())
    assert not missing, f"Harness boundary files disappeared: {missing}"
    return sorted(files)


def _is_legacy_compatibility_file(path: Path) -> bool:
    return path.name in LEGACY_COMPATIBILITY_FILENAMES


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


def _domain_literals_and_identifiers(tree: ast.AST) -> set[str]:
    values: set[str] = set()
    docstrings = _docstring_constant_ids(tree)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            values.add(node.value)
        elif isinstance(node, ast.Name):
            values.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            values.add(node.name)
        elif isinstance(node, ast.Attribute):
            values.add(node.attr)
    return values


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
    return path.relative_to(SERVICES_DIR.parent.parent).as_posix()


def test_general_harness_and_adapters_do_not_import_professional_governance():
    violations: list[str] = []
    for path in _boundary_files():
        if _is_legacy_compatibility_file(path):
            continue
        for imported in sorted(_imports(_parse(path))):
            if imported.startswith(PROFESSIONAL_MODULE_PREFIXES):
                violations.append(f"{_relative(path)} imports {imported}")

    assert not violations, (
        "General Harness/Adapter modules must not reverse-import professional "
        "governance modules. Only explicitly named legacy compatibility files "
        "are exempt:\n" + "\n".join(violations)
    )


def test_general_harness_and_adapters_contain_no_domain_contract_constants():
    violations: list[str] = []
    for path in _boundary_files():
        if _is_legacy_compatibility_file(path):
            continue
        values = _domain_literals_and_identifiers(_parse(path))
        matched = {
            label
            for label, pattern in DOMAIN_PATTERNS.items()
            if any(pattern.search(value) for value in values)
        }
        if matched:
            violations.append(f"{_relative(path)}: {', '.join(sorted(matched))}")

    assert not violations, (
        "Harness/Adapter contracts must stay domain-neutral; move these rules "
        "behind an explicit governance node or a named legacy compatibility "
        "adapter:\n" + "\n".join(violations)
    )
