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

from app.config import settings
from app.schemas.workspace_analysis import ScopeCandidate

AgentStatus = Literal[
    "ok", "unavailable", "timeout", "invalid_output", "rejected_command", "error"
]
AgentGoal = Literal["source_scope", "coverage_entry"]

SOURCE_EXTS = frozenset({
    ".c", ".h", ".cc", ".cpp", ".hpp", ".py", ".go", ".rs", ".java",
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
    return {
        "command": command,
        "status": "available",
        "argv": apply_readonly_cli_guard(provider, resolved_argv),
        "executable": executable,
        "path": resolved,
        "launch_kind": "exec",
    }


def split_agent_command(command: str) -> list[str]:
    value = (command or "").strip()
    if not value:
        return []
    try:
        return shlex.split(value, posix=os.name != "nt")
    except ValueError:
        return value.split()


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
                powershell,
                "-NoLogo",
                "-NonInteractive",
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
    return None


def _windows_shell_agent_argv(argv: list[str]) -> list[str]:
    powershell = _find_powershell() or "powershell.exe"
    base = [powershell, "-NoLogo", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
    if not settings.external_agent_windows_shell_load_profile:
        base.append("-NoProfile")
    quoted = " ".join(_powershell_single_quote(item) for item in argv)
    script = (
        "$__codetalkPrompt = [Console]::In.ReadToEnd(); "
        f"$__codetalkPrompt | & {quoted}"
    )
    return [*base, "-Command", script]


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
        return [part.strip() for part in re.split(r"[;\n]+", value) if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(part).strip() for part in value if str(part).strip()]
    return [str(value).strip()] if str(value).strip() else []


def validate_agent_candidate_file(
    repo_path: str | Path,
    path: str,
    *,
    allow_directory_candidates: bool = True,
) -> CandidateValidation:
    root = Path(repo_path).resolve()
    raw = (path or "").strip().strip('"')
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
    for item in payload.get("candidate_files") or []:
        if not isinstance(item, dict):
            continue
        candidate = AgentCandidateFile(
            path=str(item.get("path") or ""),
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
    for item in payload.get("candidate_entries") or []:
        if not isinstance(item, dict):
            continue
        chain = item.get("chain") or []
        entry = AgentCandidateEntry(
            entry_kind=str(item.get("entry_kind") or item.get("entry_type") or "external"),
            entry_symbol=str(item.get("entry_symbol") or item.get("symbol") or ""),
            entry_file=str(item.get("entry_file") or item.get("file") or "") or None,
            chain=[str(x) for x in chain if x],
            external_trigger=str(item.get("external_trigger") or ""),
            reason=str(item.get("reason") or ""),
        )
        if entry.entry_file:
            validation = validate_agent_candidate_file(repo_path, entry.entry_file)
            entry.validated = validation.validated
            entry.validation_error = validation.validation_error
            if validation.path:
                entry.entry_file = validation.path
        else:
            entry.validated = bool(entry.entry_symbol and entry.chain)
        entries.append(entry)

    commands = [str(c) for c in payload.get("commands") or [] if c]
    need_source_slices = [
        {
            "file_path": str(item.get("file_path") or item.get("path") or ""),
            "symbol": str(item.get("symbol") or "") or None,
            "reason": str(item.get("reason") or ""),
        }
        for item in (payload.get("need_source_slices") or [])
        if isinstance(item, dict)
    ]
    return AgentDiscoveryResult(
        provider=provider,
        status="ok",
        candidate_files=files,
        candidate_symbols=[s for s in payload.get("candidate_symbols") or [] if isinstance(s, dict)],
        candidate_entries=entries,
        need_source_slices=need_source_slices,
        commands=commands,
        raw_summary=str(payload.get("raw_summary") or payload.get("summary") or "")[:4000],
        warnings=[str(w) for w in payload.get("warnings") or [] if w],
    )


def _extract_cli_error(raw: str) -> str | None:
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
    message = payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return _unwrap_agent_payload(_json_loads_flexible(content))
        if isinstance(content, list):
            text = "\n".join(
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict)
            ).strip()
            if text:
                return _unwrap_agent_payload(_json_loads_flexible(text))
    return payload


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


def _extract_first_json_object(raw: str) -> str | None:
    start = raw.find("{")
    if start < 0:
        return None
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
                return raw[start:index + 1]
    return None


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
        by_key[validation.path.lower()] = cand.model_copy(update={"path": validation.resolved_path or cand.path})

    for result in agent_results:
        if result.status != "ok":
            if result.status != "unavailable":
                detail = result.warnings[0] if result.warnings else ""
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
            0 if c.source == "external_agent" else 1,
            0 if c.confidence == "high" else 1,
            str(c.path or "").lower(),
        ),
    )
    return merged, warnings


async def run_external_agent_discovery(
    request: AgentDiscoveryRequest,
    providers: list[str] | None = None,
    session: object | None = None,
) -> list[AgentDiscoveryResult]:
    if not settings.external_agents_enabled:
        return []
    selected = (providers or list(PROVIDER_COMMANDS))[: max(1, settings.external_agent_max_parallel)]
    tasks = [_run_provider(provider, request, session=session) for provider in selected]
    return await asyncio.gather(*tasks)


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
    health = check_provider_health(provider, command, fallback_commands=fallback_commands)
    if health.get("status") != "available":
        reason = _format_unavailable_health_summary(health)
        result = AgentDiscoveryResult(
            provider=provider,
            status="unavailable",
            raw_summary=reason,
            warnings=[reason] if reason else [],
        )
        _record_agent_turn(session, provider, request, "", result.raw_summary, result)
        return result
    prompt = build_agent_prompt(request)
    argv = [str(item) for item in health.get("argv") or []]
    if not argv:
        result = AgentDiscoveryResult(provider=provider, status="unavailable", raw_summary="empty command")
        _record_agent_turn(session, provider, request, prompt, result.raw_summary, result)
        return result
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
        result = AgentDiscoveryResult(provider=provider, status="error", raw_summary=str(exc))
        _record_agent_turn(session, provider, request, prompt, result.raw_summary, result)
        return result

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
        result = AgentDiscoveryResult(provider=provider, status="timeout", raw_summary="timeout")
        _record_agent_turn(session, provider, request, prompt, result.raw_summary, result)
        return result
    raw = stdout.decode("utf-8", errors="replace")
    stderr_text = stderr.decode("utf-8", errors="replace")
    if proc.returncode not in {0, None}:
        summary = _format_process_error_summary(proc.returncode, stderr_text, raw)
        result = AgentDiscoveryResult(
            provider=provider,
            status="error",
            raw_summary=summary,
            warnings=[summary],
        )
        _record_agent_turn(session, provider, request, prompt, raw + stderr_text, result)
        return result
    if not raw.strip() and stderr:
        result = AgentDiscoveryResult(
            provider=provider,
            status="error",
            raw_summary=stderr_text[:4000],
        )
        _record_agent_turn(session, provider, request, prompt, result.raw_summary, result)
        return result
    result = parse_agent_output(provider, raw, request.repo_path)
    if health.get("used_fallback"):
        result.warnings.append(str(health.get("reason") or "used fallback agent command"))
    _record_agent_turn(session, provider, request, prompt, raw, result)
    return result


def _format_process_error_summary(returncode: int | None, stderr_text: str, stdout_text: str) -> str:
    parts = [f"external agent exited with exit code {returncode}"]
    stderr_text = (stderr_text or "").strip()
    stdout_text = (stdout_text or "").strip()
    if stderr_text:
        parts.append(f"stderr: {stderr_text[:3000]}")
    if stdout_text:
        parts.append(f"stdout: {stdout_text[:1000]}")
    return "; ".join(parts)[:4000]


def _format_unavailable_health_summary(health: dict) -> str:
    parts = [str(health.get("reason") or "").strip()]
    diagnostic = health.get("diagnostic")
    if isinstance(diagnostic, dict):
        parts.append(str(diagnostic.get("summary") or "").strip())
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
