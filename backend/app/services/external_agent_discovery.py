"""Read-only external-agent source and entry discovery.

CodeTalk remains the judge: Claude Code / OpenCode may suggest files and
entries, but only locally validated repository paths enter formal evidence.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

from app.config import settings
from app.schemas.workspace_analysis import ScopeCandidate

AgentStatus = Literal[
    "ok", "unavailable", "timeout", "invalid_output", "rejected_command", "error"
]
AgentGoal = Literal["source_scope", "coverage_entry"]

SOURCE_EXTS = frozenset({
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".py", ".go", ".rs", ".java",
    ".ts", ".tsx", ".js", ".jsx",
})

PROVIDER_COMMANDS = {
    "claude-code": "claude_code_command",
    "opencode": "opencode_command",
}

PROVIDER_FALLBACK_COMMANDS = {
    "claude-code": "claude_code_fallback_commands",
    "opencode": "opencode_fallback_commands",
}

PROVIDER_READONLY_ARGS = {
    "claude-code": "claude_code_readonly_args",
    "opencode": "opencode_readonly_args",
}

DISCOVERY_SCHEMA_KEYS = frozenset({
    "candidate_files",
    "candidate_symbols",
    "candidate_entries",
    "need_source_slices",
    "commands",
})


@dataclass
class AgentCandidateFile:
    path: str
    reason: str = ""
    confidence: str = "medium"
    evidence_excerpt: str = ""
    validated: bool = False
    validation_error: str | None = None


@dataclass
class AgentCandidateEntry:
    entry_kind: str
    entry_symbol: str
    entry_file: str | None = None
    chain: list[str] = field(default_factory=list)
    external_trigger: str = ""
    input_hints: list[str] = field(default_factory=list)
    reason: str = ""
    validated: bool = False
    validation_error: str | None = None


@dataclass
class AgentDiscoveryRequest:
    request_id: str
    repo_path: str
    analysis_object_text: str
    path_hints: list[str] = field(default_factory=list)
    scope_hints: list[dict] = field(default_factory=list)
    coverage_hit: dict | None = None
    existing_candidates: list[dict] = field(default_factory=list)
    context_packet: dict | None = None
    goal: AgentGoal = "source_scope"


@dataclass
class AgentDiscoveryResult:
    provider: str
    status: AgentStatus
    turn_id: str | None = None
    candidate_files: list[AgentCandidateFile] = field(default_factory=list)
    candidate_symbols: list[dict] = field(default_factory=list)
    candidate_entries: list[AgentCandidateEntry] = field(default_factory=list)
    need_source_slices: list[dict] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    raw_summary: str = ""
    warnings: list[str] = field(default_factory=list)
    runtime_attempts: list[dict] = field(default_factory=list)


@dataclass
class CandidateValidation:
    input_path: str
    path: str | None = None
    resolved_path: str | None = None
    validated: bool = False
    validation_error: str | None = None


def expand_agent_query_terms(text: str) -> list[str]:
    """Expand fuzzy module names such as ``nvme-tcp-tls``.

    The important domain alias here is ``nvme`` <-> ``nvmf``; path discovery
    must also try the known ``transport/tls`` shape.
    """
    original = (text or "").strip()
    raw_parts = [p.lower() for p in re.split(r"[-_/\\\s]+", original) if p.strip()]
    seen: set[str] = set()
    out: list[str] = []

    def add(value: str) -> None:
        value = value.strip().replace("\\", "/")
        if not value or value in seen:
            return
        seen.add(value)
        out.append(value)

    add(original.lower())
    for part in raw_parts:
        add(part)
    if raw_parts:
        add("_".join(raw_parts))
        add("/".join(raw_parts))

    variants: list[list[str]] = [[]]
    aliases = {"nvme": ["nvme", "nvmf"], "nvmf": ["nvmf", "nvme"]}
    for part in raw_parts:
        choices = aliases.get(part, [part])
        variants = [base + [choice] for base in variants for choice in choices]
    for variant in variants:
        if not variant:
            continue
        add("_".join(variant))
        add("/".join(variant))
        if len(variant) >= 2 and variant[-2:] == ["tcp", "tls"]:
            add("transport/tls")
            add(f"{variant[0]}_tcp/transport/tls")
            add(f"{variant[0]}_tcp_tls")
        if "tls" in variant:
            add("tls")
    return out[:48]


def check_provider_health(
    provider: str,
    command: str,
    fallback_commands: list[str] | None = None,
) -> dict:
    attempts: list[dict] = []
    commands = [command, *(fallback_commands or [])]
    for index, candidate_command in enumerate(commands):
        attempt = _resolve_provider_command_attempt(candidate_command, provider=provider)
        attempts.append(attempt)
        if attempt.get("status") != "available":
            continue
        health = {
            "provider": provider,
            "status": "available",
            "command": " ".join(attempt["argv"]),
            "configured_command": candidate_command,
            "argv": attempt["argv"],
            "configured_argv": attempt.get("configured_argv"),
            "path": attempt["path"],
            "launch_kind": attempt.get("launch_kind") or "exec",
            "used_fallback": index > 0,
            "attempts": attempts,
        }
        if index > 0:
            health["reason"] = f"primary command unavailable; using fallback: {candidate_command}"
        return health

    attempted = ", ".join(str(cmd).strip() for cmd in commands if str(cmd).strip()) or "<empty>"
    diagnostic = _agent_runtime_diagnostic()
    return {
        "provider": provider,
        "status": "unavailable",
        "reason": f"no agent command found; attempted: {attempted}",
        "attempts": attempts,
        "diagnostic": diagnostic,
    }


def _provider_candidate_commands(command: str, fallback_commands: list[str] | None = None) -> list[str]:
    return [str(item).strip() for item in [command, *(fallback_commands or [])] if str(item).strip()]


def _fallback_reason(candidate_command: str, prior_attempts: list[dict]) -> str:
    if any(str(item.get("probe_status") or item.get("run_status") or "") for item in prior_attempts):
        return f"primary command failed; using fallback: {candidate_command}"
    return f"primary command unavailable; using fallback: {candidate_command}"


def _agent_result_diagnostic(result: AgentDiscoveryResult) -> str:
    if result.status == "ok":
        return result.raw_summary or (result.warnings[0] if result.warnings else result.status)
    return result.warnings[0] if result.warnings else (result.raw_summary or result.status)


def _runtime_attempt_records(
    provider: str,
    request_id: str,
    attempts: list[dict],
    *,
    phase: str,
) -> list[dict]:
    fields_to_keep = (
        "command",
        "status",
        "reason",
        "executable",
        "path",
        "launch_kind",
        "run_status",
        "run_message",
        "probe_status",
        "probe_message",
    )
    records: list[dict] = []
    for index, attempt in enumerate(attempts, start=1):
        if not isinstance(attempt, dict):
            continue
        record = {
            "kind": "runtime_attempt",
            "object_id": request_id,
            "provider": provider,
            "phase": phase,
            "attempt_index": index,
        }
        for key in fields_to_keep:
            value = attempt.get(key)
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                record[key] = _redact_agent_diagnostic_text(str(value)) if isinstance(value, str) else value
            else:
                record[key] = _redact_agent_diagnostic_text(str(value))
        records.append(record)
    return records


_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(--?(?:api[-_]?key|token|access[-_]?token|secret|password)(?:\s+|=))(['\"]?)([^\s\"']+)(['\"]?)"
)
_SECRET_KV_RE = re.compile(
    r"(?i)\b((?:api[-_]?key|token|access[-_]?token|secret|password)=)(['\"]?)([^\s\"']+)(['\"]?)"
)
_BEARER_RE = re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]{8,})")
_OPENAI_STYLE_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9._-]{6,}\b")


def _redact_agent_diagnostic_text(value: str) -> str:
    text = value
    text = _SECRET_ASSIGNMENT_RE.sub(r"\1\2<redacted>\4", text)
    text = _SECRET_KV_RE.sub(r"\1\2<redacted>\4", text)
    text = _BEARER_RE.sub(r"\1<redacted>", text)
    text = _OPENAI_STYLE_KEY_RE.sub("<redacted>", text)
    return text


def redact_agent_diagnostic_text(value: str) -> str:
    return _redact_agent_diagnostic_text(value)


def _redact_agent_diagnostics(value: object) -> object:
    if isinstance(value, str):
        return _redact_agent_diagnostic_text(value)
    if isinstance(value, list):
        return [_redact_agent_diagnostics(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_agent_diagnostics(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact_agent_diagnostics(item) for key, item in value.items()}
    return value


def _redact_probe_response(payload: dict) -> dict:
    return _redact_agent_diagnostics(payload) if isinstance(payload, dict) else payload


def _unavailable_health_from_attempts(
    provider: str,
    commands: list[str],
    attempts: list[dict],
) -> dict:
    attempted = ", ".join(commands) or "<empty>"
    return {
        "provider": provider,
        "status": "unavailable",
        "reason": f"no agent command found; attempted: {attempted}",
        "attempts": attempts,
        "diagnostic": _agent_runtime_diagnostic(),
    }


def _resolve_provider_command_attempt(command: str, provider: str | None = None) -> dict:
    argv = split_agent_command(command)
    if not argv:
        return {
            "command": command,
            "argv": [],
            "status": "unavailable",
            "reason": "empty command",
        }
    executable = argv[0]
    configured_path = _resolve_configured_executable_path(executable)
    if configured_path:
        resolved_argv = [configured_path, *argv[1:]]
        guarded_argv = apply_readonly_cli_guard(provider, resolved_argv)
        if _is_windows_powershell_script(configured_path):
            return {
                "command": command,
                "status": "available",
                "argv": _windows_shell_agent_argv(guarded_argv),
                "configured_argv": guarded_argv,
                "executable": executable,
                "path": configured_path,
                "launch_kind": "powershell-script",
            }
        return {
            "command": command,
            "status": "available",
            "argv": guarded_argv,
            "executable": executable,
            "path": configured_path,
            "launch_kind": "exec",
        }
    if platform.system().lower().startswith("win"):
        resolved = shutil.which(executable)
        if not resolved:
            where = shutil.which("where.exe")
            if where:
                try:
                    proc = subprocess.run(
                        [where, executable], capture_output=True, text=True, timeout=3
                    )
                    first = (proc.stdout or "").splitlines()[0].strip() if proc.stdout else ""
                    resolved = first or None
                except Exception:
                    resolved = None
        if not resolved:
            resolved = _resolve_windows_common_command_path(executable)
    else:
        resolved = shutil.which(executable)
    if not resolved:
        shell_resolution = _probe_windows_shell_command(executable)
        if shell_resolution:
            guarded_argv = apply_readonly_cli_guard(provider, argv)
            shell_argv = _windows_shell_agent_argv(guarded_argv)
            return {
                "command": command,
                "status": "available",
                "argv": shell_argv,
                "configured_argv": guarded_argv,
                "executable": executable,
                "path": shell_resolution,
                "launch_kind": "powershell",
            }
        return {
            "command": command,
            "argv": argv,
            "executable": executable,
            "status": "unavailable",
            "reason": f"command not found: {executable}",
        }
    resolved_argv = [resolved, *argv[1:]]
    guarded_argv = apply_readonly_cli_guard(provider, resolved_argv)
    if _is_windows_powershell_script(resolved):
        return {
            "command": command,
            "status": "available",
            "argv": _windows_shell_agent_argv(guarded_argv),
            "configured_argv": guarded_argv,
            "executable": executable,
            "path": resolved,
            "launch_kind": "powershell-script",
        }
    return {
        "command": command,
        "status": "available",
        "argv": guarded_argv,
        "executable": executable,
        "path": resolved,
        "launch_kind": "exec",
    }


def _resolve_configured_executable_path(executable: str) -> str | None:
    value = (executable or "").strip().strip('"').strip("'")
    if not value or not any(sep in value for sep in ("/", "\\")):
        return None
    candidate = Path(value).expanduser()
    try:
        if candidate.is_file():
            return str(candidate.resolve())
    except OSError:
        return None
    return None


def _is_windows_powershell_script(path: str) -> bool:
    return platform.system().lower().startswith("win") and Path(path).suffix.lower() == ".ps1"


def split_agent_command(command: str) -> list[str]:
    value = (command or "").strip()
    if not value:
        return []
    try:
        parts = shlex.split(value, posix=os.name != "nt")
    except ValueError:
        parts = value.split()
    return [_strip_wrapping_quotes(part) for part in parts]


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _resolve_windows_common_command_path(executable: str) -> str | None:
    """Find user-level command shims that service PATH often misses on Windows."""
    value = (executable or "").strip().strip('"')
    if not value or any(sep in value for sep in ("/", "\\")):
        return None

    base_dirs: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        base_dirs.append(Path(appdata) / "npm")
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        base_dirs.append(Path(userprofile) / "AppData" / "Roaming" / "npm")

    suffix = Path(value).suffix
    names = [value] if suffix else [
        f"{value}.cmd",
        f"{value}.exe",
        f"{value}.bat",
        value,
        f"{value}.ps1",
    ]
    seen_dirs: set[str] = set()
    for base_dir in base_dirs:
        key = str(base_dir).lower()
        if key in seen_dirs:
            continue
        seen_dirs.add(key)
        for name in names:
            candidate = base_dir / name
            if candidate.is_file():
                return str(candidate)
    return None


def _agent_runtime_diagnostic(max_path_entries: int = 12) -> dict:
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = "<unavailable>"
    path_env = os.environ.get("PATH") or ""
    all_entries = [part for part in path_env.split(os.pathsep) if part]
    visible_entries = all_entries[:max(0, max_path_entries)]
    path_summary = " | ".join(visible_entries) if visible_entries else "<empty>"
    if len(all_entries) > len(visible_entries):
        path_summary = f"{path_summary} | ... (+{len(all_entries) - len(visible_entries)} more)"
    return {
        "cwd": cwd,
        "path_entries": visible_entries,
        "path_entry_count": len(all_entries),
        "summary": f"cwd: {cwd}; PATH entries: {path_summary}",
    }


def _probe_windows_shell_command(executable: str) -> str | None:
    if not settings.external_agent_windows_shell_fallback_enabled:
        return None
    if not platform.system().lower().startswith("win"):
        return None
    powershell = _find_powershell()
    if not powershell:
        return None
    try:
        proc = subprocess.run(
            [
                *_windows_powershell_base_argv(powershell),
                "-Command",
                (
                    "$cmd = Get-Command -ErrorAction SilentlyContinue "
                    f"{_powershell_single_quote(executable)}; "
                    "if ($cmd) { $cmd.Source; if (-not $cmd.Source) { $cmd.Definition } }"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    summary = (proc.stdout or "").strip()
    return summary or f"PowerShell command: {executable}"


def _find_powershell() -> str | None:
    for name in ("powershell.exe", "pwsh.exe"):
        found = shutil.which(name)
        if found:
            return found
    if platform.system().lower().startswith("win"):
        for env_name in ("SystemRoot", "WINDIR"):
            root = os.environ.get(env_name)
            if not root:
                continue
            candidate = (
                Path(root)
                / "System32"
                / "WindowsPowerShell"
                / "v1.0"
                / "powershell.exe"
            )
            try:
                if candidate.is_file():
                    return str(candidate)
            except OSError:
                continue
    return None


def _windows_shell_agent_argv(argv: list[str]) -> list[str]:
    powershell = _find_powershell() or "powershell.exe"
    base = _windows_powershell_base_argv(powershell)
    quoted = " ".join(_powershell_single_quote(item) for item in argv)
    script = (
        "$__codetalkPrompt = [Console]::In.ReadToEnd(); "
        f"$__codetalkPrompt | & {quoted}"
    )
    return [*base, "-Command", script]


def _windows_powershell_base_argv(powershell: str) -> list[str]:
    base = [powershell, "-NoLogo", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
    if not settings.external_agent_windows_shell_load_profile:
        base.append("-NoProfile")
    return base


def _powershell_single_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def provider_fallback_commands(provider: str) -> list[str]:
    attr = PROVIDER_FALLBACK_COMMANDS.get(provider)
    if not attr:
        return []
    return _coerce_command_list(getattr(settings, attr, []))


def provider_readonly_args(provider: str | None) -> list[str]:
    if not provider:
        return []
    attr = PROVIDER_READONLY_ARGS.get(provider)
    if not attr:
        return []
    return _coerce_command_list(getattr(settings, attr, []))


def apply_readonly_cli_guard(provider: str | None, argv: list[str]) -> list[str]:
    if not settings.external_agent_enforce_readonly_cli:
        return list(argv)
    return _append_missing_option_chunks(list(argv), provider_readonly_args(provider))


def _append_missing_option_chunks(argv: list[str], extra_args: list[str]) -> list[str]:
    if not extra_args:
        return argv
    result = list(argv)
    index = 0
    while index < len(extra_args):
        token = extra_args[index]
        chunk = [token]
        index += 1
        while index < len(extra_args) and not extra_args[index].startswith("-"):
            chunk.append(extra_args[index])
            index += 1
        if token.startswith("-") and token in result:
            continue
        result.extend(chunk)
    return result


def _coerce_command_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _split_command_list_string(value)
    if isinstance(value, (list, tuple, set)):
        return [str(part).strip() for part in value if str(part).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _coerce_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        return [str(part).strip() for part in value if str(part).strip()]
    text = str(value).strip()
    return [text] if text else []


def _coerce_dict_items(value: object) -> list[dict]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if isinstance(item, dict)]
    return []


def _candidate_path_value(item: dict, *preferred_keys: str) -> str:
    keys = [
        *preferred_keys,
        "path",
        "file_path",
        "file",
        "source_file",
        "source_path",
    ]
    for key in keys:
        value = item.get(key)
        if value:
            return str(value)
    return ""


def _split_command_list_string(value: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    for char in value:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            current.append(char)
            escaped = True
            continue
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            current.append(char)
            quote = char
            continue
        if char in {";", "\n", "\r"}:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            continue
        current.append(char)
    part = "".join(current).strip()
    if part:
        parts.append(part)
    return parts


def validate_agent_candidate_file(
    repo_path: str | Path,
    path: str,
    *,
    allow_directory_candidates: bool = True,
) -> CandidateValidation:
    root = Path(repo_path).resolve()
    raw = _normalize_agent_path_text(path)
    if not raw:
        return CandidateValidation(input_path=path, validation_error="empty_path")
    normalized = raw.replace("\\", "/")
    candidate = Path(normalized)
    if not candidate.is_absolute():
        candidate = root.joinpath(*[part for part in normalized.split("/") if part])

    resolved = _resolve_existing_or_suffix(root, candidate, normalized)
    if resolved is None:
        return CandidateValidation(input_path=path, validation_error="file_not_found")
    try:
        resolved = resolved.resolve()
        rel = resolved.relative_to(root)
    except Exception:
        return CandidateValidation(input_path=path, validation_error="outside_repo")
    if resolved.is_dir():
        if not allow_directory_candidates:
            return CandidateValidation(
                input_path=path,
                resolved_path=str(resolved),
                path=rel.as_posix(),
                validation_error="directory_candidate_not_allowed",
            )
        source_file = _preferred_source_file_under(resolved)
        if source_file is None:
            return CandidateValidation(
                input_path=path,
                resolved_path=str(resolved),
                path=rel.as_posix(),
                validation_error="directory_without_source_file",
            )
        resolved = source_file.resolve()
        rel = resolved.relative_to(root)
    if resolved.suffix.lower() not in SOURCE_EXTS:
        return CandidateValidation(
            input_path=path,
            resolved_path=str(resolved),
            path=rel.as_posix(),
            validation_error="non_source_file",
        )
    return CandidateValidation(
        input_path=path,
        path=rel.as_posix(),
        resolved_path=str(resolved),
        validated=True,
    )


def _normalize_agent_path_text(path: str) -> str:
    raw = (path or "").strip().strip('"').strip("'").strip("`")
    raw = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", raw).strip()
    raw = re.sub(r"(?i)^(?:path|file|source|entry_file)\s*:\s+", "", raw).strip()
    markdown_match = re.fullmatch(r"\[[^\]]+\]\(([^)]+)\)", raw)
    if markdown_match:
        raw = markdown_match.group(1).strip()
    raw = raw.strip("<>")
    raw = _normalize_file_uri_path(raw)
    raw = re.sub(
        rf"(?i)({'|'.join(re.escape(ext) for ext in SOURCE_EXTS)}(?:\:\d+(?:\:\d+|-\d+)?|#L\d+(?:-L\d+)?)?)[,.;]+$",
        lambda match: match.group(1),
        raw,
    )
    raw = re.sub(
        rf"(?i)({'|'.join(re.escape(ext) for ext in SOURCE_EXTS)})(?::\d+(?::\d+|-\d+)?|#L\d+(?:-L\d+)?)$",
        lambda match: match.group(1),
        raw,
    )
    return raw


def _normalize_file_uri_path(raw: str) -> str:
    parsed = urlparse(raw)
    if parsed.scheme.lower() != "file":
        return raw
    path = unquote(parsed.path or "")
    if parsed.netloc and parsed.netloc.lower() != "localhost":
        path = f"//{parsed.netloc}{path}"
    if re.match(r"^/[A-Za-z]:/", path):
        path = path[1:]
    return path


def _resolve_existing_or_suffix(root: Path, candidate: Path, normalized: str) -> Path | None:
    try:
        if candidate.exists():
            return candidate
    except OSError:
        return None
    suffixes = _candidate_suffixes_for_root(root, normalized)
    if not suffixes:
        return None
    matches: list[Path] = []
    for walk_root, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "build", "dist"}]
        for name in files:
            full = Path(walk_root) / name
            rel = full.relative_to(root).as_posix().lower()
            if any(rel.endswith(suffix) for suffix in suffixes):
                matches.append(full)
    matches.sort(key=lambda p: len(p.relative_to(root).parts))
    return matches[0] if matches else None


def _preferred_source_file_under(directory: Path) -> Path | None:
    source_files: list[Path] = []
    for walk_root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "build", "dist"}]
        for name in files:
            full = Path(walk_root) / name
            if full.suffix.lower() in SOURCE_EXTS:
                source_files.append(full)
    if not source_files:
        return None
    priority = {
        ".c": 0,
        ".cc": 0,
        ".cpp": 0,
        ".cxx": 0,
        ".h": 1,
        ".hpp": 1,
        ".py": 2,
        ".go": 2,
        ".rs": 2,
        ".java": 2,
        ".ts": 2,
        ".tsx": 2,
        ".js": 2,
        ".jsx": 2,
    }
    source_files.sort(key=lambda p: (
        len(p.relative_to(directory).parts),
        priority.get(p.suffix.lower(), 9),
        p.name.lower(),
        p.as_posix().lower(),
    ))
    return source_files[0]


def _candidate_suffixes_for_root(root: Path, normalized: str) -> list[str]:
    suffix = normalized.strip("/").lower()
    if not suffix:
        return []
    parts = [part for part in suffix.split("/") if part]
    suffixes = [suffix]
    root_name = root.name.lower()
    for index, part in enumerate(parts):
        if part == root_name and index + 1 < len(parts):
            suffixes.append("/".join(parts[index + 1:]))
    return list(dict.fromkeys(suffixes))


def parse_agent_output(provider: str, raw_output: str, repo_path: str | Path) -> AgentDiscoveryResult:
    raw = (raw_output or "")[: settings.external_agent_max_output_chars]
    cli_error = _extract_cli_error(raw)
    if cli_error:
        return AgentDiscoveryResult(
            provider=provider,
            status="error",
            raw_summary=cli_error,
            warnings=[cli_error],
        )
    try:
        payload = _load_agent_json_payload(raw)
    except json.JSONDecodeError as exc:
        return AgentDiscoveryResult(
            provider=provider,
            status="invalid_output",
            raw_summary=_invalid_output_summary(raw),
            warnings=[_invalid_output_warning(raw, exc)],
        )
    if not isinstance(payload, dict):
        return AgentDiscoveryResult(
            provider=provider,
            status="invalid_output",
            raw_summary=raw,
            warnings=["agent JSON root must be an object"],
        )
    if not _has_discovery_schema(payload):
        return AgentDiscoveryResult(
            provider=provider,
            status="invalid_output",
            raw_summary=json.dumps(payload, ensure_ascii=False)[:4000],
            warnings=["agent JSON schema missing discovery fields"],
        )

    files: list[AgentCandidateFile] = []
    for item in _coerce_dict_items(payload.get("candidate_files")):
        candidate = AgentCandidateFile(
            path=_candidate_path_value(item),
            reason=str(item.get("reason") or ""),
            confidence=_normalize_confidence(item.get("confidence")),
            evidence_excerpt=str(item.get("evidence_excerpt") or ""),
        )
        validation = validate_agent_candidate_file(repo_path, candidate.path)
        candidate.validated = validation.validated
        candidate.validation_error = validation.validation_error
        if validation.path:
            candidate.path = validation.path
        files.append(candidate)

    entries: list[AgentCandidateEntry] = []
    for item in _coerce_dict_items(payload.get("candidate_entries")):
        entry = AgentCandidateEntry(
            entry_kind=str(item.get("entry_kind") or item.get("entry_type") or "external"),
            entry_symbol=str(item.get("entry_symbol") or item.get("symbol") or ""),
            entry_file=_candidate_path_value(item, "entry_file") or None,
            chain=_coerce_string_list(item.get("chain")),
            external_trigger=str(item.get("external_trigger") or ""),
            input_hints=_coerce_string_list(item.get("input_hints")),
            reason=str(item.get("reason") or ""),
        )
        if entry.entry_file:
            validation = validate_agent_candidate_file(
                repo_path,
                entry.entry_file,
                allow_directory_candidates=False,
            )
            entry.validated = validation.validated
            entry.validation_error = validation.validation_error
            if validation.path:
                entry.entry_file = validation.path
        else:
            entry.validated = False
            entry.validation_error = "entry_file_missing"
        entries.append(entry)

    commands = _coerce_string_list(payload.get("commands"))
    need_source_slices = [
        {
            "file_path": _candidate_path_value(item, "file_path"),
            "symbol": str(item.get("symbol") or "") or None,
            "reason": str(item.get("reason") or ""),
        }
        for item in _coerce_dict_items(payload.get("need_source_slices"))
    ]
    return AgentDiscoveryResult(
        provider=provider,
        status="ok",
        candidate_files=files,
        candidate_symbols=_coerce_dict_items(payload.get("candidate_symbols")),
        candidate_entries=entries,
        need_source_slices=need_source_slices,
        commands=commands,
        raw_summary=str(payload.get("raw_summary") or payload.get("summary") or "")[:4000],
        warnings=_coerce_string_list(payload.get("warnings")),
    )


def _extract_cli_error(raw: str) -> str | None:
    for candidate in _iter_json_objects(raw or ""):
        error = _extract_cli_error_from_payload_text(candidate)
        if error:
            return error
    return _extract_cli_error_from_payload_text((raw or "").strip())


def _extract_cli_error_from_payload_text(raw: str) -> str | None:
    try:
        payload = json.loads((raw or "").strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("is_error") is not True and not str(payload.get("subtype") or "").startswith("error"):
        return None
    parts = [
        str(payload.get("subtype") or "").strip(),
        str(payload.get("api_error_status") or "").strip(),
        str(payload.get("result") or payload.get("error") or "").strip(),
    ]
    summary = "; ".join(part for part in parts if part)
    return summary[:4000] if summary else "external agent reported an error"


def _load_agent_json_payload(raw: str) -> object:
    raw = (raw or "").strip()
    payload = _json_loads_flexible(raw)
    return _unwrap_agent_payload(payload)


def _json_loads_flexible(raw: str) -> object:
    if not raw:
        raise json.JSONDecodeError("empty output", raw, 0)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as original_exc:
        fenced = _extract_fenced_json(raw)
        if fenced is not None:
            return json.loads(fenced)
        discovery = _extract_discovery_json_object(raw)
        if discovery is not None:
            return json.loads(discovery)
        balanced = _extract_first_json_object(raw)
        if balanced is not None:
            return json.loads(balanced)
        raise original_exc


def _unwrap_agent_payload(payload: object) -> object:
    if not isinstance(payload, dict):
        return payload
    if _has_discovery_schema(payload):
        return payload
    result = payload.get("result")
    if isinstance(result, dict):
        return _unwrap_agent_payload(result)
    if isinstance(result, str):
        return _unwrap_agent_payload(_json_loads_flexible(result))
    unwrapped = _unwrap_agent_content(payload.get("content"))
    if unwrapped is not None:
        return unwrapped
    output_text = payload.get("output_text")
    unwrapped = _unwrap_agent_content(output_text)
    if unwrapped is not None:
        return unwrapped
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            unwrapped = _unwrap_agent_content(item.get("content"))
            if unwrapped is not None:
                return unwrapped
            unwrapped = _unwrap_agent_content(item.get("text"))
            if unwrapped is not None:
                return unwrapped
    message = payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        unwrapped = _unwrap_agent_content(content)
        if unwrapped is not None:
            return unwrapped
    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            choice_message = choice.get("message")
            if isinstance(choice_message, dict):
                unwrapped = _unwrap_agent_content(choice_message.get("content"))
                if unwrapped is not None:
                    return unwrapped
            delta = choice.get("delta")
            if isinstance(delta, dict):
                unwrapped = _unwrap_agent_content(delta.get("content"))
                if unwrapped is not None:
                    return unwrapped
    return payload


def _unwrap_agent_content(content: object) -> object | None:
    if isinstance(content, str) and content.strip():
        return _unwrap_agent_payload(_json_loads_flexible(content))
    if isinstance(content, list):
        text = "\n".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict)
        ).strip()
        if text:
            return _unwrap_agent_payload(_json_loads_flexible(text))
    return None


def _has_discovery_schema(payload: dict) -> bool:
    return any(key in payload for key in DISCOVERY_SCHEMA_KEYS)


def _invalid_output_summary(raw: str) -> str:
    wrapper_text = _extract_wrapper_text_result(raw)
    if wrapper_text:
        return wrapper_text[:4000]
    return (raw or "")[:4000]


def _invalid_output_warning(raw: str, exc: json.JSONDecodeError) -> str:
    if _extract_wrapper_text_result(raw):
        return f"agent output did not contain discovery JSON: {exc}"
    return f"invalid JSON: {exc}"


def _extract_wrapper_text_result(raw: str) -> str | None:
    try:
        payload = json.loads((raw or "").strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    return result.strip() if isinstance(result, str) and result.strip() else None


def _extract_fenced_json(raw: str) -> str | None:
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else None


def _extract_discovery_json_object(raw: str) -> str | None:
    for candidate in _iter_json_objects(raw):
        try:
            payload = _unwrap_agent_payload(json.loads(candidate))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and _has_discovery_schema(payload):
            return candidate
    return None


def _extract_first_json_object(raw: str) -> str | None:
    for candidate in _iter_json_objects(raw):
        return candidate
    return None


def _iter_json_objects(raw: str):
    start = raw.find("{")
    while start >= 0:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(raw)):
            char = raw[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    yield raw[start:index + 1]
                    start = raw.find("{", index + 1)
                    break
        else:
            return


def merge_source_candidates(
    repo_path: str | Path,
    existing: list[ScopeCandidate],
    agent_results: list[AgentDiscoveryResult],
) -> tuple[list[ScopeCandidate], list[str]]:
    by_key: dict[str, ScopeCandidate] = {}
    providers_by_key: dict[str, set[str]] = {}
    warnings: list[str] = []

    for cand in existing:
        if not cand.path:
            continue
        validation = validate_agent_candidate_file(repo_path, cand.path)
        if not validation.validated or not validation.path:
            continue
        key = validation.path.lower()
        normalized = cand.model_copy(update={"path": validation.resolved_path or cand.path})
        if key in by_key:
            by_key[key] = _merge_existing_source_candidate(by_key[key], normalized)
        else:
            by_key[key] = normalized

    for result in agent_results:
        if result.status != "ok":
            if result.status != "unavailable":
                detail = result.warnings[0] if result.warnings else result.raw_summary
                suffix = f" - {detail}" if detail else ""
                warnings.append(f"{result.provider}: {result.status}{suffix}")
            continue
        for file in result.candidate_files:
            validation = validate_agent_candidate_file(repo_path, file.path)
            if not validation.validated or not validation.path:
                warnings.append(
                    f"{result.provider}: rejected {file.path} ({validation.validation_error or file.validation_error})"
                )
                continue
            key = validation.path.lower()
            providers_by_key.setdefault(key, set()).add(result.provider)
            reason = f"external agent {result.provider}: {file.reason or 'validated source path'}"
            confidence = "high" if file.confidence == "high" else "medium"
            if key in by_key and by_key[key].source == "external_agent":
                if len(providers_by_key[key]) > 1:
                    confidence = "high"
                by_key[key] = by_key[key].model_copy(update={
                    "confidence": confidence,
                    "reason": by_key[key].reason + f"; {result.provider} also matched",
                })
            elif key in by_key and _source_candidate_priority(by_key[key].source) <= _source_candidate_priority("external_agent"):
                existing_confidence = by_key[key].confidence
                by_key[key] = by_key[key].model_copy(update={
                    "confidence": "high" if "high" in {existing_confidence, confidence} else existing_confidence,
                    "reason": by_key[key].reason + f"; {result.provider} also matched",
                })
            else:
                by_key[key] = ScopeCandidate(
                    path=validation.resolved_path,
                    source="external_agent",
                    confidence=confidence,
                    reason=reason,
                    role="primary",
                )

    merged = sorted(
        by_key.values(),
        key=lambda c: (
            _source_candidate_priority(c.source),
            0 if c.confidence == "high" else 1,
            str(c.path or "").lower(),
        ),
    )
    return merged, warnings


def _source_candidate_priority(source: str) -> int:
    return {
        "manual": 0,
        "repo_search": 0,
        "external_agent": 1,
        "gitnexus": 2,
        "material": 3,
    }.get(str(source or ""), 9)


def _merge_existing_source_candidate(current: ScopeCandidate, incoming: ScopeCandidate) -> ScopeCandidate:
    current_priority = _source_candidate_priority(current.source)
    incoming_priority = _source_candidate_priority(incoming.source)
    keep = current if current_priority <= incoming_priority else incoming
    other = incoming if keep is current else current
    confidence = "high" if "high" in {keep.confidence, other.confidence} else keep.confidence
    other_source = str(other.source or "candidate")
    other_reason = str(other.reason or "also matched")
    return keep.model_copy(update={
        "confidence": confidence,
        "reason": f"{keep.reason}; {other_source} also matched: {other_reason}",
    })


async def run_external_agent_discovery(
    request: AgentDiscoveryRequest,
    providers: list[str] | None = None,
    session: object | None = None,
) -> list[AgentDiscoveryResult]:
    if not settings.external_agents_enabled:
        return []
    selected = (providers or list(PROVIDER_COMMANDS))[: max(1, settings.external_agent_max_parallel)]
    tasks = [_run_provider(provider, request, session=session) for provider in selected]
    gathered = await asyncio.gather(*tasks, return_exceptions=True)
    results: list[AgentDiscoveryResult] = []
    for provider, item in zip(selected, gathered):
        if isinstance(item, asyncio.CancelledError):
            raise item
        if isinstance(item, Exception):
            summary = str(item).strip() or item.__class__.__name__
            result = AgentDiscoveryResult(
                provider=provider,
                status="error",
                raw_summary=summary,
                warnings=[summary],
            )
            _record_agent_turn(session, provider, request, "", summary, result)
            results.append(result)
            continue
        results.append(item)
    return results


async def probe_external_agent_startup(
    provider: str,
    repo_path: str | Path | None = None,
) -> dict:
    """Start one provider with a minimal stdin probe and report diagnostics."""
    command_attr = PROVIDER_COMMANDS.get(provider)
    if not command_attr:
        return _redact_probe_response({
            "provider": provider,
            "healthy": False,
            "status": "unavailable",
            "message": "unknown provider",
        })

    command = str(getattr(settings, command_attr, "") or "")
    cwd = Path(repo_path or os.getcwd()).resolve()
    if not cwd.exists() or not cwd.is_dir():
        return _redact_probe_response({
            "provider": provider,
            "healthy": False,
            "status": "error",
            "message": f"probe cwd does not exist: {cwd}",
        })

    prompt = _build_startup_probe_prompt()
    commands = _provider_candidate_commands(command, provider_fallback_commands(provider))
    attempts: list[dict] = []
    last_failure: dict | None = None
    last_unavailable_health: dict | None = None

    for index, candidate_command in enumerate(commands):
        health = check_provider_health(provider, candidate_command, fallback_commands=[])
        candidate_attempts = [
            item for item in (health.get("attempts") or []) if isinstance(item, dict)
        ]
        if not candidate_attempts:
            candidate_attempts = [{
                "command": candidate_command,
                "status": health.get("status") or "unavailable",
            }]
        attempts.extend(candidate_attempts)
        attempt = candidate_attempts[-1]
        if health.get("status") != "available":
            last_unavailable_health = dict(health)
            last_unavailable_health["attempts"] = list(attempts)
            continue

        health = dict(health)
        health["used_fallback"] = index > 0
        health["attempts"] = list(attempts)
        if index > 0:
            health["reason"] = _fallback_reason(candidate_command, attempts[:-1])
        argv = [str(item) for item in health.get("argv") or []]
        env = os.environ.copy()
        env["CODETALK_AGENT_READONLY"] = "1"
        env["CODETALK_REPO_PATH"] = str(cwd)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except OSError as exc:
            message = str(exc)
            attempt["probe_status"] = "error"
            attempt["probe_message"] = message
            last_failure = {
                "provider": provider,
                "healthy": False,
                "status": "error",
                "message": message,
                "health": health,
            }
            continue

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")),
                timeout=max(1, settings.external_agent_startup_probe_timeout_sec),
            )
            await _wait_for_process_exit(proc)
        except asyncio.CancelledError:
            await _kill_and_wait_process(proc)
            raise
        except asyncio.TimeoutError:
            await _kill_and_wait_process(proc)
            message = "startup probe timed out"
            attempt["probe_status"] = "timeout"
            attempt["probe_message"] = message
            last_failure = {
                "provider": provider,
                "healthy": False,
                "status": "timeout",
                "message": message,
                "health": health,
            }
            continue

        raw = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        if proc.returncode not in {0, None}:
            message = _format_process_error_summary(proc.returncode, stderr_text, raw)
            attempt["probe_status"] = "error"
            attempt["probe_message"] = message[:4000]
            last_failure = {
                "provider": provider,
                "healthy": False,
                "status": "error",
                "message": message,
                "health": health,
                "stderr": stderr_text[:4000],
                "stdout": raw[:4000],
            }
            continue

        result = parse_agent_output(provider, raw, cwd)
        message = _agent_result_diagnostic(result)
        attempt["probe_status"] = result.status
        attempt["probe_message"] = message[:4000]
        if result.status != "ok" and index < len(commands) - 1:
            last_failure = {
                "provider": provider,
                "healthy": False,
                "status": result.status,
                "message": message[:4000],
                "health": health,
                "warnings": result.warnings,
                "stdout": raw[:4000],
                "stderr": stderr_text[:4000],
            }
            continue
        health["attempts"] = list(attempts)
        if health.get("used_fallback"):
            health["reason"] = _fallback_reason(candidate_command, attempts[:-1])
        return _redact_probe_response({
            "provider": provider,
            "healthy": result.status == "ok",
            "status": result.status,
            "message": message[:4000],
            "health": health,
            "warnings": result.warnings,
            "stdout": raw[:4000],
            "stderr": stderr_text[:4000],
        })

    health = last_unavailable_health or _unavailable_health_from_attempts(provider, commands, attempts)
    health["attempts"] = list(attempts)
    if last_failure:
        last_failure["health"] = health | {"status": "available", "attempts": attempts}
        return _redact_probe_response(last_failure)
    message = _format_unavailable_health_summary(health)
    return _redact_probe_response({
        "provider": provider,
        "healthy": False,
        "status": "unavailable",
        "message": message,
        "health": health,
    })


def _build_startup_probe_prompt() -> str:
    return (
        "CodeTalk external-agent startup probe. Do not inspect files, do not run commands, "
        "do not use network, and do not modify anything. Return ONLY this JSON object with "
        "the same schema and no markdown:\n"
        '{"candidate_files":[],"candidate_symbols":[],"candidate_entries":[],'
        '"need_source_slices":[],"commands":[],"raw_summary":"startup_probe_ok"}'
    )


async def _run_provider(
    provider: str,
    request: AgentDiscoveryRequest,
    *,
    session: object | None = None,
) -> AgentDiscoveryResult:
    command_attr = PROVIDER_COMMANDS.get(provider)
    if not command_attr:
        return AgentDiscoveryResult(provider=provider, status="unavailable", raw_summary="unknown provider")
    command = str(getattr(settings, command_attr, "") or "")
    fallback_commands = provider_fallback_commands(provider)
    prompt = build_agent_prompt(request)
    commands = _provider_candidate_commands(command, fallback_commands)
    attempts: list[dict] = []
    prior_failures: list[str] = []
    last_result: AgentDiscoveryResult | None = None
    last_unavailable_health: dict | None = None
    last_raw = ""

    for index, candidate_command in enumerate(commands):
        health = check_provider_health(provider, candidate_command, fallback_commands=[])
        candidate_attempts = [
            item for item in (health.get("attempts") or []) if isinstance(item, dict)
        ]
        if not candidate_attempts:
            candidate_attempts = [{
                "command": candidate_command,
                "status": health.get("status") or "unavailable",
            }]
        attempts.extend(candidate_attempts)
        attempt = candidate_attempts[-1]
        if health.get("status") != "available":
            last_unavailable_health = dict(health)
            last_unavailable_health["attempts"] = list(attempts)
            continue

        health = dict(health)
        health["used_fallback"] = index > 0
        health["attempts"] = list(attempts)
        if index > 0:
            health["reason"] = _fallback_reason(candidate_command, attempts[:-1])
        argv = [str(item) for item in health.get("argv") or []]
        if not argv:
            summary = "empty command"
            attempt["run_status"] = "unavailable"
            attempt["run_message"] = summary
            prior_failures.append(summary)
            last_result = AgentDiscoveryResult(provider=provider, status="unavailable", raw_summary=summary)
            continue

        env = os.environ.copy()
        env["CODETALK_AGENT_READONLY"] = "1"
        env["CODETALK_REPO_PATH"] = str(Path(request.repo_path).resolve())
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=request.repo_path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except OSError as exc:
            summary = _format_spawn_error_summary(exc, health)
            attempt["run_status"] = "error"
            attempt["run_message"] = summary[:4000]
            prior_failures.append(summary)
            last_result = AgentDiscoveryResult(
                provider=provider,
                status="error",
                raw_summary=summary,
                warnings=[summary],
            )
            last_raw = summary
            continue

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")),
                timeout=max(1, settings.external_agent_timeout_sec),
            )
            await _wait_for_process_exit(proc)
        except asyncio.CancelledError:
            await _kill_and_wait_process(proc)
            raise
        except asyncio.TimeoutError:
            await _kill_and_wait_process(proc)
            summary = "timeout"
            attempt["run_status"] = "timeout"
            attempt["run_message"] = summary
            prior_failures.append(summary)
            last_result = AgentDiscoveryResult(provider=provider, status="timeout", raw_summary=summary)
            last_raw = summary
            continue

        raw = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        if proc.returncode not in {0, None}:
            cli_error = _extract_cli_error(raw) or _extract_cli_error(stderr_text)
            summary = cli_error or _format_process_error_summary(proc.returncode, stderr_text, raw)
            attempt["run_status"] = "error"
            attempt["run_message"] = summary[:4000]
            prior_failures.append(summary)
            last_result = AgentDiscoveryResult(
                provider=provider,
                status="error",
                raw_summary=summary,
                warnings=[summary],
            )
            last_raw = raw + stderr_text
            continue
        if not raw.strip() and stderr:
            summary = stderr_text[:4000]
            attempt["run_status"] = "error"
            attempt["run_message"] = summary
            prior_failures.append(summary)
            last_result = AgentDiscoveryResult(
                provider=provider,
                status="error",
                raw_summary=summary,
            )
            last_raw = summary
            continue

        result = parse_agent_output(provider, raw, request.repo_path)
        attempt["run_status"] = result.status
        attempt["run_message"] = _agent_result_diagnostic(result)[:4000]
        if result.status != "ok" and index < len(commands) - 1:
            summary = _agent_result_diagnostic(result)
            prior_failures.append(summary)
            last_result = result
            last_raw = raw
            continue
        if health.get("used_fallback"):
            result.warnings.append(str(health.get("reason") or "used fallback agent command"))
        for failure in prior_failures:
            if failure and failure not in result.warnings:
                result.warnings.append(failure[:4000])
        result.runtime_attempts = _runtime_attempt_records(
            provider,
            request.request_id,
            attempts,
            phase="discovery",
        )
        _record_agent_turn(session, provider, request, prompt, raw, result)
        return result

    health = last_unavailable_health or _unavailable_health_from_attempts(provider, commands, attempts)
    health["attempts"] = list(attempts)
    if last_result is not None:
        for failure in prior_failures:
            if failure and failure not in last_result.warnings:
                last_result.warnings.append(failure[:4000])
        last_result.runtime_attempts = _runtime_attempt_records(
            provider,
            request.request_id,
            attempts,
            phase="discovery",
        )
        _record_agent_turn(session, provider, request, prompt, last_raw or last_result.raw_summary, last_result)
        return last_result

    reason = _format_unavailable_health_summary(health)
    result = AgentDiscoveryResult(
        provider=provider,
        status="unavailable",
        raw_summary=reason,
        warnings=[reason] if reason else [],
        runtime_attempts=_runtime_attempt_records(
            provider,
            request.request_id,
            attempts,
            phase="discovery",
        ),
    )
    _record_agent_turn(session, provider, request, "", result.raw_summary, result)
    return result


def _format_process_error_summary(returncode: int | None, stderr_text: str, stdout_text: str) -> str:
    parts = [f"external agent exited with exit code {returncode}"]
    stderr_text = (stderr_text or "").strip()
    stdout_text = (stdout_text or "").strip()
    if stderr_text:
        parts.append(f"stderr: {_redact_agent_diagnostic_text(stderr_text)[:3000]}")
    if stdout_text:
        parts.append(f"stdout: {_redact_agent_diagnostic_text(stdout_text)[:1000]}")
    return "; ".join(parts)[:4000]


def _format_spawn_error_summary(exc: OSError, health: dict) -> str:
    parts = [_redact_agent_diagnostic_text(str(exc).strip()) or "external agent spawn failed"]
    launch = str(health.get("launch_kind") or "").strip()
    if launch:
        parts.append(f"launch={launch}")
    configured = str(health.get("configured_command") or "").strip()
    if configured:
        parts.append(f"configured={_redact_agent_diagnostic_text(configured)}")
    path = str(health.get("path") or "").strip()
    if path:
        parts.append(f"path={_redact_agent_diagnostic_text(path)}")
    configured_argv = health.get("configured_argv")
    if isinstance(configured_argv, list) and configured_argv:
        argv_summary = " ".join(_redact_agent_diagnostic_text(str(item)) for item in configured_argv)
        parts.append("configured_argv=" + argv_summary[:1000])
    attempts = health.get("attempts")
    if isinstance(attempts, list) and attempts:
        attempt_summary = ", ".join(
            _format_health_attempt_for_error(attempt)
            for attempt in attempts
            if isinstance(attempt, dict)
        )
        if attempt_summary:
            parts.append(f"attempts={attempt_summary[:1500]}")
    diagnostic = health.get("diagnostic")
    if isinstance(diagnostic, dict):
        diag = str(diagnostic.get("summary") or "").strip()
        if diag:
            parts.append(_redact_agent_diagnostic_text(diag))
    return "; ".join(part for part in parts if part)[:4000]


def _format_health_attempt_for_error(attempt: dict) -> str:
    command = _redact_agent_diagnostic_text(str(attempt.get("command") or "").strip())
    status = str(attempt.get("status") or "").strip()
    launch = str(attempt.get("launch_kind") or "").strip()
    path = _redact_agent_diagnostic_text(str(attempt.get("path") or "").strip())
    details = [command]
    if status:
        details.append(f"status={status}")
    if launch:
        details.append(f"launch={launch}")
    if path:
        details.append(f"path={path}")
    return " ".join(part for part in details if part)


def _format_unavailable_health_summary(health: dict) -> str:
    parts = [_redact_agent_diagnostic_text(str(health.get("reason") or "").strip())]
    diagnostic = health.get("diagnostic")
    if isinstance(diagnostic, dict):
        parts.append(_redact_agent_diagnostic_text(str(diagnostic.get("summary") or "").strip()))
    return "; ".join(part for part in parts if part)[:4000]


async def _kill_and_wait_process(proc: object) -> None:
    try:
        kill = getattr(proc, "kill")
        kill()
    except ProcessLookupError:
        return
    except Exception:
        return
    await _wait_for_process_exit(proc)


async def _wait_for_process_exit(proc: object, timeout: float = 5) -> None:
    wait = getattr(proc, "wait", None)
    if wait is None:
        return
    try:
        async with asyncio.timeout(timeout):
            await wait()
        await _yield_windows_subprocess_cleanup()
    except Exception:
        return


async def _yield_windows_subprocess_cleanup() -> None:
    if platform.system().lower().startswith("win"):
        await asyncio.sleep(0.05)


def build_agent_prompt(request: AgentDiscoveryRequest) -> str:
    if request.context_packet:
        base = _sanitize_context_packet({
            "request_id": request.request_id,
            "context_packet": request.context_packet,
        })
    else:
        base = {
            "request_id": request.request_id,
            "repo_path": str(Path(request.repo_path).resolve()),
            "analysis_object_text": request.analysis_object_text,
            "path_hints": request.path_hints,
            "scope_hints": request.scope_hints,
            "coverage_hit": request.coverage_hit,
            "existing_candidates": request.existing_candidates[:20],
            "goal": request.goal,
            "expanded_terms": expand_agent_query_terms(request.analysis_object_text),
        }
    if request.goal == "coverage_entry":
        task = (
            "Find source-backed external entries for the uncovered function. "
            "Look for RPC/API/CLI/config/message/timer/callback registration, "
            "return a call chain and an externally constructible trigger."
        )
    else:
        task = (
            "Find the source scope for a fuzzy module name. Search paths and "
            "directory names first, then source content. Try nvme/nvmf, "
            "nvme_tcp/nvmf_tcp, transport/tls, and tls variants."
        )
    schema = {
        "candidate_files": [
            {"path": "repo/relative/source.c", "reason": "...", "confidence": "high|medium|low", "evidence_excerpt": "..."}
        ],
        "candidate_symbols": [{"symbol": "...", "file": "...", "reason": "..."}],
        "candidate_entries": [
            {
                "entry_kind": "rpc|api|cli|config|message|timer|callback|external",
                "entry_symbol": "...",
                "entry_file": "repo/relative/source.c",
                "chain": ["external_entry", "target_function"],
                "external_trigger": "...",
                "input_hints": ["externally controllable request/config/message values"],
                "reason": "...",
            }
        ],
        "commands": ["rg --files"],
        "raw_summary": "short summary",
        "need_source_slices": [
            {"file_path": "repo/relative/source.c", "symbol": "optional", "reason": "why more source is needed"}
        ],
        "warnings": [],
    }
    return (
        "You are a read-only source discovery agent for CodeTalk.\n"
        f"Task: {task}\n"
        "Rules: do not modify files, create tests, install dependencies, use network, "
        "commit, checkout, reset, delete, move, or copy files. Allowed commands are "
        f"{', '.join(settings.external_agent_command_allowlist)}. Python is allowed "
        "only as python -c for read-only scripts. Output JSON only, no markdown.\n"
        "Only return real source files inside the repo. Do not return guessed paths. "
        "Do not produce final vulnerability or test conclusions; return evidence.\n"
        f"Request JSON:\n{json.dumps(base, ensure_ascii=False, indent=2)}\n"
        f"Response schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n"
    )


def _normalize_confidence(value: object) -> str:
    text = str(value or "medium").lower()
    if text in {"high", "medium", "low"}:
        return text
    return "medium"


def _sanitize_context_packet(value: object) -> object:
    forbidden = {"raw_summary", "raw_output", "raw_outputs", "prompt", "prompts"}
    if isinstance(value, dict):
        return {
            str(key): _sanitize_context_packet(item)
            for key, item in value.items()
            if str(key) not in forbidden
        }
    if isinstance(value, list):
        return [_sanitize_context_packet(item) for item in value]
    return value


def _record_agent_turn(
    session: object | None,
    provider: str,
    request: AgentDiscoveryRequest,
    prompt: str,
    raw_output: str,
    result: AgentDiscoveryResult,
) -> None:
    if session is None or not hasattr(session, "record_turn"):
        return
    try:
        runtime_attempts = [
            dict(item)
            for item in getattr(result, "runtime_attempts", []) or []
            if isinstance(item, dict)
        ]
        ledger = getattr(session, "ledger", None)
        command_history = getattr(ledger, "command_history", None)
        if isinstance(command_history, list):
            existing_keys = {
                (
                    str(item.get("object_id") or ""),
                    str(item.get("provider") or ""),
                    str(item.get("phase") or ""),
                    str(item.get("attempt_index") or ""),
                    str(item.get("command") or ""),
                )
                for item in command_history
                if isinstance(item, dict) and item.get("kind") == "runtime_attempt"
            }
            for attempt in runtime_attempts:
                key = (
                    str(attempt.get("object_id") or ""),
                    str(attempt.get("provider") or ""),
                    str(attempt.get("phase") or ""),
                    str(attempt.get("attempt_index") or ""),
                    str(attempt.get("command") or ""),
                )
                if key not in existing_keys:
                    command_history.append(attempt)
                    existing_keys.add(key)
        turn = session.record_turn(
            provider=provider,
            goal=request.goal,
            prompt=prompt,
            raw_output=raw_output,
            parsed_result=asdict(result),
            validation_result={
                "validated_files": sum(1 for item in result.candidate_files if item.validated),
                "rejected_files": sum(1 for item in result.candidate_files if not item.validated),
                "validated_entries": sum(1 for item in result.candidate_entries if item.validated),
                "rejected_entries": sum(1 for item in result.candidate_entries if not item.validated),
                "need_source_slices": len(result.need_source_slices),
            },
            status=result.status,
        )
        result.turn_id = getattr(turn, "turn_id", None)
    except Exception:
        return
