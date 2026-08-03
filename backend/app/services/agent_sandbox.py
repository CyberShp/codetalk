from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import stat
import sys
import tempfile
import urllib.parse
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tomllib

from app.config import settings


class AgentSandboxError(RuntimeError):
    pass


CredentialFingerprint = tuple[int, str]


@dataclass
class BenchmarkSandboxSecurity:
    credential_fingerprints: set[CredentialFingerprint] = field(default_factory=set)


@dataclass(frozen=True)
class _BenchmarkSandboxPolicy:
    source_dir: Path
    model: str
    mode: str
    approved_network_targets: tuple[str, ...]
    security: BenchmarkSandboxSecurity


_BENCHMARK_SANDBOX_POLICY: ContextVar[_BenchmarkSandboxPolicy | None] = ContextVar(
    "quality_benchmark_sandbox_policy", default=None
)


@contextmanager
def benchmark_agent_sandbox(
    *,
    source_dir: Path,
    model: str,
    mode: str,
    approved_network_targets: tuple[str, ...] = (),
):
    """Opt one synchronous Workbench execution into the strict benchmark boundary."""

    source = Path(source_dir).resolve(strict=True)
    if not source.is_dir():
        raise AgentSandboxError("benchmark source boundary is not a directory")
    if mode not in {"rapid", "deep"}:
        raise AgentSandboxError("benchmark sandbox mode must be rapid or deep")
    normalized_model = str(model).strip()
    if not normalized_model or len(normalized_model) > 200 or any(
        ord(char) < 32 for char in normalized_model
    ):
        raise AgentSandboxError("benchmark model identifier is invalid")
    normalized_targets = tuple(
        _validated_benchmark_network_target(item)
        for item in approved_network_targets
    )
    security = BenchmarkSandboxSecurity()
    token = _BENCHMARK_SANDBOX_POLICY.set(
        _BenchmarkSandboxPolicy(
            source, normalized_model, mode, normalized_targets, security
        )
    )
    try:
        yield security
    finally:
        _BENCHMARK_SANDBOX_POLICY.reset(token)


_CODEX_RUNTIME_CONFIG_KEYS = (
    "model",
    "model_provider",
    "model_reasoning_effort",
    "model_context_window",
    "model_auto_compact_token_limit",
    "disable_response_storage",
    "network_access",
    "service_tier",
)

_BENCHMARK_AUTH_TOP_LEVEL_KEYS = (
    "auth_mode",
    "last_refresh",
    "OPENAI_API_KEY",
)
_BENCHMARK_AUTH_TOKEN_KEYS = (
    "access_token",
    "account_id",
    "id_token",
    "refresh_token",
)
_BENCHMARK_AUTH_MAX_BYTES = 1024 * 1024


def credential_value_fingerprints(
    values: Iterable[str],
) -> tuple[CredentialFingerprint, ...]:
    fingerprints: set[CredentialFingerprint] = set()
    for value in values:
        if not isinstance(value, str) or len(value) < 8:
            continue
        raw = value.encode("utf-8")
        quoted = urllib.parse.quote(value, safe="")
        quoted_plus = urllib.parse.quote_plus(value, safe="")
        variants = {
            value,
            base64.b64encode(raw).decode("ascii"),
            base64.urlsafe_b64encode(raw).decode("ascii"),
            base64.urlsafe_b64encode(raw).decode("ascii").rstrip("="),
            raw.hex(),
            raw.hex().upper(),
            "".join(f"%{byte:02X}" for byte in raw),
            "".join(f"%{byte:02x}" for byte in raw),
            quoted,
            quoted_plus,
            re.sub(r"%[0-9A-F]{2}", lambda match: match.group(0).lower(), quoted),
            re.sub(
                r"%[0-9A-F]{2}",
                lambda match: match.group(0).lower(),
                quoted_plus,
            ),
        }
        fingerprints.update(
            (len(variant), hashlib.sha256(variant.encode("utf-8")).hexdigest())
            for variant in variants
            if len(variant) >= 8
        )
    return tuple(sorted(fingerprints))

_CODEX_SKILLS_MAX_FILES = 4096
_CODEX_SKILLS_MAX_ENTRIES = 4096
_CODEX_SKILLS_MAX_BYTES = 64 * 1024 * 1024
_CODEX_SKILLS_MAX_DEPTH = 12


def _write_sanitized_codex_config(source: Path, target: Path) -> None:
    try:
        payload = tomllib.loads(source.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return
    lines: list[str] = []
    for key in _CODEX_RUNTIME_CONFIG_KEYS:
        value = payload.get(key)
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            rendered = str(value)
        elif isinstance(value, str):
            rendered = json.dumps(value, ensure_ascii=False)
        else:
            continue
        lines.append(f"{key} = {rendered}")
    if lines:
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _copy_codex_skills_tree(source: Path, target: Path) -> None:
    source_root = source.resolve(strict=True)
    copied_files = 0
    copied_entries = 0
    copied_bytes = 0

    def same_identity(left: os.stat_result, right: os.stat_result) -> bool:
        return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)

    def open_verified_entry(
        parent_fd: int,
        name: str,
        expected: os.stat_result,
        *,
        directory: bool,
        display_path: Path,
    ) -> tuple[int, os.stat_result]:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        if directory:
            flags |= getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(name, flags, dir_fd=parent_fd)
        except OSError as exc:
            raise AgentSandboxError(
                f"Codex skills 条目在复制前发生变化：{display_path}"
            ) from exc
        try:
            opened = os.fstat(descriptor)
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            expected_kind = stat.S_ISDIR if directory else stat.S_ISREG
            if (
                not expected_kind(opened.st_mode)
                or not same_identity(expected, opened)
                or not same_identity(opened, current)
            ):
                raise AgentSandboxError(
                    f"Codex skills 条目在复制前发生变化：{display_path}"
                )
            return descriptor, opened
        except Exception:
            os.close(descriptor)
            raise

    def copy_regular_file(
        source_fd: int,
        target_path: Path,
        source_mode: int,
    ) -> None:
        nonlocal copied_bytes
        try:
            with os.fdopen(source_fd, "rb", closefd=True) as source_stream:
                with target_path.open("xb") as target_stream:
                    while True:
                        chunk = source_stream.read(1024 * 1024)
                        if not chunk:
                            break
                        copied_bytes += len(chunk)
                        if copied_bytes > _CODEX_SKILLS_MAX_BYTES:
                            raise AgentSandboxError(
                                "Codex skills 总大小超过安全上限 64 MiB。"
                            )
                        target_stream.write(chunk)
        except Exception:
            target_path.unlink(missing_ok=True)
            raise
        owner_mode = stat.S_IRUSR | stat.S_IWUSR
        if source_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            owner_mode |= stat.S_IXUSR
        target_path.chmod(owner_mode)

    def copy_directory(
        current_fd: int,
        current_target: Path,
        depth: int,
        relative_path: Path,
    ) -> None:
        nonlocal copied_entries, copied_files
        if depth > _CODEX_SKILLS_MAX_DEPTH:
            raise AgentSandboxError(
                f"Codex skills 目录层级超过安全上限 {_CODEX_SKILLS_MAX_DEPTH}。"
            )
        current_target.mkdir(mode=0o700)
        current_target.chmod(0o700)
        with os.scandir(current_fd) as iterator:
            entries = []
            remaining_entries = _CODEX_SKILLS_MAX_ENTRIES - copied_entries
            for entry in iterator:
                entries.append(entry)
                if len(entries) > remaining_entries:
                    raise AgentSandboxError(
                        f"Codex skills 目录项超过安全上限 {_CODEX_SKILLS_MAX_ENTRIES}。"
                    )
            entries.sort(key=lambda entry: entry.name)
        for entry in entries:
            copied_entries += 1
            if copied_entries > _CODEX_SKILLS_MAX_ENTRIES:
                raise AgentSandboxError(
                    f"Codex skills 目录项超过安全上限 {_CODEX_SKILLS_MAX_ENTRIES}。"
                )
            entry_relative = relative_path / entry.name
            entry_target = current_target / entry.name
            if entry.is_symlink():
                raise AgentSandboxError(
                    f"Codex skills 不允许包含符号链接：{entry_relative}"
                )
            entry_stat = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(entry_stat.st_mode):
                child_fd, _ = open_verified_entry(
                    current_fd,
                    entry.name,
                    entry_stat,
                    directory=True,
                    display_path=entry_relative,
                )
                try:
                    copy_directory(child_fd, entry_target, depth + 1, entry_relative)
                finally:
                    os.close(child_fd)
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                raise AgentSandboxError(
                    f"Codex skills 包含不支持的文件类型：{entry_relative}"
                )
            copied_files += 1
            if copied_files > _CODEX_SKILLS_MAX_FILES:
                raise AgentSandboxError(
                    f"Codex skills 文件数超过安全上限 {_CODEX_SKILLS_MAX_FILES}。"
                )
            source_fd, opened_stat = open_verified_entry(
                current_fd,
                entry.name,
                entry_stat,
                directory=False,
                display_path=entry_relative,
            )
            copy_regular_file(source_fd, entry_target, opened_stat.st_mode)

    try:
        supports_descriptor_walk = (
            os.name == "posix"
            and os.open in os.supports_dir_fd
            and os.stat in os.supports_dir_fd
        )
        if not supports_descriptor_walk:
            target.mkdir(mode=0o700)
            target.chmod(0o700)
        else:
            root_flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            root_fd = os.open(source_root, root_flags)
            try:
                if not stat.S_ISDIR(os.fstat(root_fd).st_mode):
                    raise AgentSandboxError("Codex skills 根目录不是安全目录。")
                copy_directory(root_fd, target, 0, Path("."))
            finally:
                os.close(root_fd)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


@dataclass(frozen=True)
class AgentSandboxLaunch:
    status: str
    wrapper: list[str]
    message: str
    audit: dict[str, Any]


_PARENT_ENV_ALLOWLIST = {
    "ALL_PROXY",
    "APPDATA",
    "CCR_CONFIG_PATH",
    "CLAUDE_CONFIG_DIR",
    "CODETALK_TEMP_DIR",
    "CODEX_HOME",
    "COMSPEC",
    "HOME",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "LANG",
    "LOCALAPPDATA",
    "LOGNAME",
    "NODE_EXTRA_CA_CERTS",
    "NO_PROXY",
    "OPENCODE_CONFIG_DIR",
    "PATH",
    "PATHEXT",
    "REQUESTS_CA_BUNDLE",
    "SHELL",
    "SSH_AUTH_SOCK",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USER",
    "USERPROFILE",
}


def filtered_agent_environment(explicit: dict[str, Any] | None = None) -> dict[str, str]:
    prefixes = ("LC_", "XDG_")
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in _PARENT_ENV_ALLOWLIST
        or key.upper().startswith(prefixes)
    }
    for key, value in (explicit or {}).items():
        name = str(key).strip()
        if name:
            env[name] = str(value)
    return env


def prepare_isolated_runtime_tmp(artifact_dir: Path) -> Path:
    artifact_root = artifact_dir.resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)
    runtime_tmp = Path(
        tempfile.mkdtemp(prefix=".runtime-tmp-", dir=artifact_root)
    )
    if runtime_tmp.is_symlink() or not runtime_tmp.is_dir():
        raise AgentSandboxError("Agent 隔离临时目录不是安全的真实目录。")
    runtime_tmp = runtime_tmp.resolve(strict=True)
    if runtime_tmp.parent != artifact_root:
        raise AgentSandboxError("Agent 隔离临时目录越过了任务产物边界。")
    return runtime_tmp


_ISOLATED_RUNTIME_PREFIXES = (
    ".runtime-tmp-",
    ".runtime-codex-home-",
    ".runtime-opencode-home-",
)


def cleanup_isolated_runtime_directories(artifact_dir: Path) -> list[str]:
    """Remove ephemeral Agent runtime state without crossing the artifact root."""
    artifact_root = artifact_dir.resolve()
    if not artifact_root.is_dir():
        return []
    removed: list[str] = []
    for candidate in artifact_root.iterdir():
        if not candidate.name.startswith(_ISOLATED_RUNTIME_PREFIXES):
            continue
        if candidate.parent.resolve() != artifact_root:
            continue
        try:
            if candidate.is_symlink() or not candidate.is_dir():
                candidate.unlink(missing_ok=True)
            else:
                shutil.rmtree(candidate)
            removed.append(candidate.name)
        except OSError:
            continue
    return sorted(removed)


def prepare_isolated_codex_home(
    *,
    provider: str,
    command: list[str],
    artifact_dir: Path,
    include_user_skills: bool = True,
) -> tuple[Path | None, list[Path]]:
    command_name = Path(command[0]).name.lower() if command else ""
    if "codex" not in command_name and "codex" not in provider.lower():
        return None, []

    source_home = Path(
        os.environ.get("CODEX_HOME") or Path.home() / ".codex"
    ).expanduser().resolve()
    artifact_root = artifact_dir.resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)
    runtime_home = Path(
        tempfile.mkdtemp(prefix=".runtime-codex-home-", dir=artifact_root)
    )
    if runtime_home.is_symlink() or not runtime_home.is_dir():
        raise AgentSandboxError("Codex 隔离运行目录不是安全的真实目录。")
    runtime_home = runtime_home.resolve(strict=True)
    if runtime_home.parent != artifact_root:
        raise AgentSandboxError("Codex 隔离运行目录越过了任务产物边界。")
    for state_name in ("sessions", "log", ".tmp", "tmp", "cache"):
        state_path = runtime_home / state_name
        state_path.mkdir()
        if state_path.is_symlink() or not state_path.is_dir():
            raise AgentSandboxError(f"Codex 状态目录不安全：{state_name}")
        if state_path.resolve(strict=True).parent != runtime_home:
            raise AgentSandboxError(f"Codex 状态目录越过任务边界：{state_name}")
    read_targets: list[Path] = []
    for name in ("auth.json", "config.toml", "skills"):
        if name == "skills" and not include_user_skills:
            continue
        source = source_home / name
        if not source.exists():
            continue
        resolved_source = source.resolve()
        target = runtime_home / name
        if name == "config.toml":
            _write_sanitized_codex_config(resolved_source, target)
            continue
        if name == "skills":
            _copy_codex_skills_tree(resolved_source, target)
            continue
        try:
            target.symlink_to(
                resolved_source,
                target_is_directory=resolved_source.is_dir(),
            )
        except OSError:
            if resolved_source.is_dir():
                shutil.copytree(resolved_source, target, symlinks=False)
            else:
                shutil.copy2(resolved_source, target)
        if resolved_source not in read_targets:
            read_targets.append(resolved_source)
    return runtime_home, read_targets


def prepare_isolated_opencode_home(
    *,
    provider: str,
    command: list[str],
    artifact_dir: Path,
    config_environment: dict[str, Any] | None = None,
    allow_artifact_writes: bool = False,
) -> tuple[Path | None, dict[str, str]]:
    command_name = Path(command[0]).name.lower() if command else ""
    if "opencode" not in command_name and "opencode" not in provider.lower():
        return None, {}

    artifact_root = artifact_dir.resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)
    config: dict[str, Any] | None = None
    if allow_artifact_writes:
        raw_config = str(
            (config_environment or {}).get("OPENCODE_CONFIG_CONTENT") or ""
        ).strip()
        if not raw_config:
            return None, {
                "OPENCODE_AUTO_SHARE": "false",
                "OPENCODE_DISABLE_AUTOUPDATE": "1",
                "OPENCODE_DISABLE_TELEMETRY": "1",
            }
        try:
            parsed_config = json.loads(raw_config)
        except json.JSONDecodeError as exc:
            raise AgentSandboxError(
                "OpenCode 隔离配置不是有效 JSON，无法安全批准任务产物目录。"
            ) from exc
        if not isinstance(parsed_config, dict):
            raise AgentSandboxError(
                "OpenCode 隔离配置必须是 JSON 对象，无法安全批准任务产物目录。"
            )
        config = parsed_config
        permission_value = config.get("permission")
        if permission_value is None:
            permissions: dict[str, Any] = {}
        elif isinstance(permission_value, str):
            permissions = {"*": permission_value}
        elif isinstance(permission_value, dict):
            permissions = dict(permission_value)
        else:
            raise AgentSandboxError(
                "OpenCode permission 配置格式无效，无法安全批准任务产物目录。"
            )
        external_value = permissions.get("external_directory")
        if external_value is not None and not isinstance(
            external_value, (str, dict)
        ):
            raise AgentSandboxError(
                "OpenCode external_directory 配置格式无效，无法安全批准任务产物目录。"
            )
        permissions["external_directory"] = {
            f"{artifact_root}/**": "allow",
        }
        config["permission"] = permissions

    runtime_home = Path(
        tempfile.mkdtemp(prefix=".runtime-opencode-home-", dir=artifact_root)
    )
    if runtime_home.is_symlink() or not runtime_home.is_dir():
        raise AgentSandboxError("OpenCode 隔离运行目录不是安全的真实目录。")
    runtime_home = runtime_home.resolve(strict=True)
    if runtime_home.parent != artifact_root:
        raise AgentSandboxError("OpenCode 隔离运行目录越过任务边界。")

    paths = {
        "HOME": runtime_home / "home",
        "OPENCODE_CONFIG_DIR": runtime_home / "config",
        "XDG_CONFIG_HOME": runtime_home / "xdg-config",
        "XDG_DATA_HOME": runtime_home / "data",
        "XDG_CACHE_HOME": runtime_home / "cache",
        "XDG_STATE_HOME": runtime_home / "state",
    }
    for path in paths.values():
        path.mkdir(mode=0o700)
        if path.is_symlink() or path.resolve(strict=True).parent != runtime_home:
            raise AgentSandboxError("OpenCode 隔离状态目录越过任务边界。")
    runtime_env = {
        **{key: str(path) for key, path in paths.items()},
        "OPENCODE_AUTO_SHARE": "false",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DISABLE_TELEMETRY": "1",
    }
    if config is not None:
        runtime_env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
            config,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return runtime_home, runtime_env


def codex_command_for_outer_sandbox(
    command: list[str],
    *,
    sandbox_active: bool,
) -> list[str]:
    result = list(command)
    if not sandbox_active or not result:
        return result
    executable = Path(result[0]).name.lower()
    if executable not in {"codex", "codex.exe"}:
        return result
    bypass_flag = "--dangerously-bypass-approvals-and-sandbox"
    if bypass_flag in result:
        return result
    try:
        exec_index = result.index("exec")
    except ValueError:
        return result
    result.insert(exec_index + 1, bypass_flag)
    return result


def prepare_agent_sandbox(
    *,
    runtime: dict[str, Any],
    cwd: str | None,
    artifact_dir: Path,
    platform_name: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> AgentSandboxLaunch:
    benchmark_policy = _BENCHMARK_SANDBOX_POLICY.get()
    if benchmark_policy is not None:
        return _prepare_benchmark_agent_sandbox(
            policy=benchmark_policy,
            runtime=runtime,
            cwd=cwd,
            artifact_dir=artifact_dir,
            platform_name=platform_name,
            which=which,
        )
    mode = str(runtime.get("sandbox_mode") or "auto").strip().lower()
    if mode not in {"auto", "required", "off"}:
        raise AgentSandboxError(f"未知 Agent 隔离模式：{mode}")
    platform = str(platform_name or sys.platform).lower()
    network_context = runtime.get("network_context")
    requires_network = bool(runtime.get("requires_network", True))
    artifact_dir = artifact_dir.resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    workspace = Path(cwd).resolve() if cwd else None
    extra_write_paths = _safe_extra_write_paths(runtime.get("sandbox_write_paths"))
    command = str(runtime.get("sandbox_command") or "").strip()
    runtime_read_paths, runtime_state_paths = _runtime_paths(runtime, command)
    write_paths = _unique_paths([artifact_dir, *runtime_state_paths, *extra_write_paths])
    system_read_paths = _system_read_paths(platform, command)
    read_paths = _unique_paths(
        [
            *system_read_paths,
            *runtime_read_paths,
            *write_paths,
            *([workspace] if workspace else []),
        ]
    )
    base_audit = {
        "version": "agent-sandbox-policy-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "off",
        "requested_mode": mode,
        "platform": platform,
        "workspace": str(workspace or ""),
        "workspace_access": "process_default",
        "read_boundary": "environment_managed",
        "artifact_dir": str(artifact_dir),
        "read_paths": [str(path) for path in read_paths],
        "runtime_state_paths": [str(path) for path in runtime_state_paths],
        "write_paths": [str(path) for path in write_paths],
        "network": "outbound_allowed" if requires_network else "not_requested",
        "network_policy": network_context.snapshot() if network_context is not None else None,
        "subprocess": "allowed_and_inherited",
        "environment": "allowlisted_parent_plus_runtime_explicit",
    }
    return _persist_launch(
        artifact_dir,
        status="disabled",
        wrapper=[],
        message="Agent OS 隔离未启用；CodeTalk 按当前运行环境直接启动 Agent。",
        audit={**base_audit, "engine": "none"},
    )


def _prepare_benchmark_agent_sandbox(
    *,
    policy: _BenchmarkSandboxPolicy,
    runtime: dict[str, Any],
    cwd: str | None,
    artifact_dir: Path,
    platform_name: str | None,
    which: Callable[[str], str | None],
) -> AgentSandboxLaunch:
    platform = str(platform_name or sys.platform).lower()
    artifact_dir = artifact_dir.resolve(strict=True)
    workspace = Path(cwd).resolve(strict=True) if cwd else None
    if workspace != policy.source_dir:
        raise AgentSandboxError("benchmark workspace differs from the pinned source boundary")
    task_artifact = _benchmark_task_artifact_root(artifact_dir)
    codex_home_text = str(runtime.get("sandbox_codex_home") or "").strip()
    if not codex_home_text:
        raise AgentSandboxError("benchmark requires an isolated CODEX_HOME")
    codex_home = Path(codex_home_text).resolve(strict=True)
    if codex_home != artifact_dir and artifact_dir not in codex_home.parents:
        raise AgentSandboxError("benchmark CODEX_HOME is outside the current task artifact")
    credential_mode, credential_fingerprints = _materialize_benchmark_codex_home(
        codex_home,
        model=policy.model,
        mode=policy.mode,
    )
    policy.security.credential_fingerprints.update(credential_fingerprints)
    state_paths = []
    for name in ("sessions", "log", ".tmp", "tmp", "cache"):
        path = codex_home / name
        path.mkdir(mode=0o700, exist_ok=True)
        state_paths.append(path.resolve(strict=True))
    command = str(runtime.get("sandbox_command") or "").strip()
    read_paths = _unique_paths(
        [
            *_benchmark_system_read_paths(platform, command),
            policy.source_dir,
            task_artifact,
            codex_home,
        ]
    )
    write_paths = _unique_paths([artifact_dir, *state_paths])
    network_context = runtime.get("network_context")
    allow_network = bool(runtime.get("requires_network", True))
    resolved_network_targets: list[str] = []
    if allow_network:
        if not policy.approved_network_targets:
            raise AgentSandboxError(
                "benchmark requires an approved network target allowlist"
            )
        if platform.startswith("linux"):
            raise AgentSandboxError(
                "benchmark target-only network enforcement is unavailable on Linux"
            )
        for target in policy.approved_network_targets:
            for resolved in _resolve_approved_proxy_targets(target):
                host, _separator, _port = resolved.rpartition(":")
                if host.lower() != "localhost":
                    raise AgentSandboxError(
                        "macOS benchmark networking requires an approved localhost proxy target"
                    )
                if resolved not in resolved_network_targets:
                    resolved_network_targets.append(resolved)
    audit = {
        "version": "agent-sandbox-policy-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "required",
        "requested_mode": str(runtime.get("sandbox_mode") or "auto"),
        "platform": platform,
        "workspace": str(workspace),
        "workspace_access": "read_only",
        "read_boundary": "benchmark_pinned_source_task_artifact_isolated_codex_home",
        "artifact_dir": str(artifact_dir),
        "read_paths": [str(path) for path in read_paths],
        "runtime_state_paths": [str(path) for path in state_paths],
        "write_paths": [str(path) for path in write_paths],
        "network": "approved_targets_only" if allow_network else "blocked",
        "approved_network_target_count": len(policy.approved_network_targets),
        "network_policy": (
            network_context.snapshot() if network_context is not None else None
        ),
        "subprocess": "allowed_and_inherited",
        "environment": "allowlisted_parent_plus_runtime_explicit",
        "benchmark_opt_in": True,
        "codex_home_credentials": credential_mode,
    }
    if platform.startswith("darwin"):
        sandbox_exec = which("sandbox-exec")
        if not sandbox_exec:
            raise AgentSandboxError(
                "benchmark requires macOS sandbox-exec; refusing unsandboxed execution"
            )
        profile_root = settings.ensure_runtime_temp_path() / "agent-sandbox-profiles"
        profile_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        profile_fd, profile_name = tempfile.mkstemp(
            prefix="benchmark-", suffix=".sb", dir=profile_root
        )
        os.close(profile_fd)
        profile_path = Path(profile_name)
        profile_path.write_text(
            _macos_profile(
                read_paths=read_paths,
                write_paths=write_paths,
                allow_network=allow_network,
                allowed_network_targets=resolved_network_targets,
            ),
            encoding="utf-8",
        )
        profile_path.chmod(0o600)
        return _persist_launch(
            artifact_dir,
            status="active",
            wrapper=[sandbox_exec, "-f", str(profile_path)],
            message="Benchmark macOS read boundary is active.",
            audit={**audit, "engine": "sandbox-exec", "profile": str(profile_path)},
        )
    if platform.startswith("linux"):
        bwrap = which("bwrap") or which("bubblewrap")
        if not bwrap:
            raise AgentSandboxError(
                "benchmark requires bubblewrap; refusing unsandboxed execution"
            )
        wrapper = [bwrap, "--die-with-parent", "--new-session", "--tmpfs", "/"]
        for path in read_paths:
            if path not in write_paths:
                wrapper.extend(["--ro-bind", str(path), str(path)])
        for path in write_paths:
            wrapper.extend(["--bind", str(path), str(path)])
        wrapper.extend(["--dev", "/dev", "--proc", "/proc", "--chdir", str(workspace)])
        if not allow_network:
            wrapper.append("--unshare-net")
        return _persist_launch(
            artifact_dir,
            status="active",
            wrapper=wrapper,
            message="Benchmark Linux read boundary is active.",
            audit={**audit, "engine": "bubblewrap"},
        )
    raise AgentSandboxError(
        "benchmark OS read isolation is unsupported on this platform"
    )


def _benchmark_system_read_paths(platform: str, command: str) -> list[Path]:
    paths = _system_read_paths(platform, "")
    if command:
        command_path = Path(command).expanduser()
        if command_path.is_absolute() and command_path.exists():
            resolved = command_path.resolve(strict=True)
            if not any(resolved == root or root in resolved.parents for root in paths):
                paths.append(resolved)
    return _unique_paths(paths)


def _validated_benchmark_network_target(value: str) -> str:
    target = str(value or "").strip()
    host, port = _proxy_target_host_port(target)
    if not host or port is None:
        raise AgentSandboxError("benchmark approved network target is invalid")
    if host == "localhost":
        return target
    try:
        if ipaddress.ip_address(host).is_loopback:
            raise AgentSandboxError(
                "benchmark approved network target may not be loopback"
            )
    except ValueError:
        pass
    return target


def _benchmark_task_artifact_root(artifact_dir: Path) -> Path:
    if artifact_dir.parent.name == "agent_runs":
        return artifact_dir.parent.parent.resolve(strict=True)
    return artifact_dir


def _materialize_benchmark_codex_home(
    codex_home: Path,
    *,
    model: str,
    mode: str,
) -> tuple[str, tuple[CredentialFingerprint, ...]]:
    auth_path = codex_home / "auth.json"
    minimal_auth, credential_values = _load_minimal_benchmark_auth(auth_path)
    if auth_path.is_symlink() or auth_path.is_file():
        auth_path.unlink()
    elif auth_path.exists():
        raise AgentSandboxError("benchmark CODEX_HOME auth path is not a regular file")
    credential_mode = "absent"
    if minimal_auth is not None:
        _write_private_json(auth_path, minimal_auth)
        credential_mode = "isolated_minimal"
    for child in codex_home.iterdir():
        if child.is_symlink():
            raise AgentSandboxError("benchmark CODEX_HOME contains an unexpected symlink")
    config_path = codex_home / "config.toml"
    try:
        payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        payload = {}
    lines = []
    for key in _CODEX_RUNTIME_CONFIG_KEYS:
        if key in {"model", "model_reasoning_effort"}:
            continue
        value = payload.get(key)
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            rendered = str(value)
        elif isinstance(value, str):
            rendered = json.dumps(value, ensure_ascii=True)
        else:
            continue
        lines.append(f"{key} = {rendered}")
    lines.extend(
        [
            f"model = {json.dumps(model, ensure_ascii=True)}",
            f'model_reasoning_effort = "{"high" if mode == "deep" else "low"}"',
        ]
    )
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    config_path.chmod(0o600)
    _assert_benchmark_codex_home_allowlist(
        codex_home, credentials_present=minimal_auth is not None
    )
    return credential_mode, credential_value_fingerprints(credential_values)


def _load_minimal_benchmark_auth(
    auth_path: Path,
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    if not auth_path.exists() and not auth_path.is_symlink():
        return None, ()
    if auth_path.is_symlink():
        source_home = Path(
            os.environ.get("CODEX_HOME") or Path.home() / ".codex"
        ).expanduser().resolve()
        expected_source = source_home / "auth.json"
        try:
            source = auth_path.resolve(strict=True)
            expected = expected_source.resolve(strict=True)
        except OSError as exc:
            raise AgentSandboxError(
                "benchmark CODEX_HOME auth source is unavailable"
            ) from exc
        if source != expected:
            raise AgentSandboxError(
                "benchmark CODEX_HOME auth symlink has an unapproved source"
            )
    elif auth_path.is_file():
        source = auth_path
    else:
        raise AgentSandboxError("benchmark CODEX_HOME auth path is not a regular file")
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(source, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > _BENCHMARK_AUTH_MAX_BYTES:
                raise AgentSandboxError("benchmark CODEX_HOME auth source is invalid")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw_auth = stream.read(_BENCHMARK_AUTH_MAX_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(raw_auth) > _BENCHMARK_AUTH_MAX_BYTES:
            raise AgentSandboxError("benchmark CODEX_HOME auth source is invalid")
        payload = json.loads(raw_auth.decode("utf-8"))
    except AgentSandboxError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgentSandboxError("benchmark CODEX_HOME auth source is invalid") from exc
    if not isinstance(payload, dict):
        raise AgentSandboxError("benchmark CODEX_HOME auth source is invalid")

    minimal: dict[str, Any] = {}
    for key in _BENCHMARK_AUTH_TOP_LEVEL_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            minimal[key] = value
    token_payload = payload.get("tokens")
    minimal_tokens: dict[str, str] = {}
    if isinstance(token_payload, dict):
        for key in _BENCHMARK_AUTH_TOKEN_KEYS:
            value = token_payload.get(key)
            if isinstance(value, str) and value:
                minimal_tokens[key] = value
    if minimal_tokens:
        minimal["tokens"] = minimal_tokens
    credential_values = tuple(
        value
        for value in (
            minimal.get("OPENAI_API_KEY"),
            *minimal_tokens.values(),
        )
        if isinstance(value, str) and len(value) >= 8
    )
    if not credential_values:
        raise AgentSandboxError(
            "benchmark CODEX_HOME auth source contains no usable credentials"
        )
    return minimal, credential_values


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = (json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)


def _assert_benchmark_codex_home_allowlist(
    codex_home: Path, *, credentials_present: bool
) -> None:
    auth_path = codex_home / "auth.json"
    if credentials_present:
        if auth_path.is_symlink() or not auth_path.is_file():
            raise AgentSandboxError(
                "benchmark CODEX_HOME isolated authentication is invalid"
            )
        if stat.S_IMODE(auth_path.stat().st_mode) != 0o600:
            raise AgentSandboxError(
                "benchmark CODEX_HOME isolated authentication permissions are invalid"
            )
    elif auth_path.exists() or auth_path.is_symlink():
        raise AgentSandboxError("benchmark CODEX_HOME authentication is unexpected")
    config_path = codex_home / "config.toml"
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise AgentSandboxError("benchmark CODEX_HOME config is invalid") from exc
    allowed_keys = set(_CODEX_RUNTIME_CONFIG_KEYS)
    if set(config) - allowed_keys:
        raise AgentSandboxError("benchmark CODEX_HOME config contains unapproved keys")
    allowed_entries = {
        "auth.json",
        "config.toml",
        "sessions",
        "log",
        ".tmp",
        "tmp",
        "cache",
    }
    if any(child.name not in allowed_entries for child in codex_home.iterdir()):
        raise AgentSandboxError("benchmark CODEX_HOME contains an unapproved entry")


def _persist_launch(
    artifact_dir: Path,
    *,
    status: str,
    wrapper: list[str],
    message: str,
    audit: dict[str, Any],
) -> AgentSandboxLaunch:
    payload = {**audit, "status": status, "message": message}
    _write_audit(artifact_dir, payload)
    return AgentSandboxLaunch(status=status, wrapper=wrapper, message=message, audit=payload)


def _write_audit(artifact_dir: Path, payload: dict[str, Any]) -> None:
    (artifact_dir / "sandbox_policy.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _safe_extra_write_paths(value: Any) -> list[Path]:
    items = value if isinstance(value, list) else []
    paths: list[Path] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        path = Path(text).expanduser().resolve()
        if path.exists() and path not in paths:
            paths.append(path)
    return paths


def _safe_read_paths(value: Any) -> list[Path]:
    items = value if isinstance(value, list) else []
    paths: list[Path] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        path = Path(text).expanduser().resolve()
        if path.exists() and path not in paths:
            paths.append(path)
    return paths


def _skill_symlink_targets(roots: list[Path | None]) -> list[Path]:
    targets: list[Path] = []
    for root in roots:
        if root is None:
            continue
        if not root.is_dir():
            continue
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_symlink():
                continue
            try:
                target = entry.resolve(strict=True)
            except OSError:
                continue
            if target not in targets:
                targets.append(target)
    return targets


def _runtime_paths(runtime: dict[str, Any], command: str) -> tuple[list[Path], list[Path]]:
    read_paths = _safe_read_paths(runtime.get("sandbox_read_paths"))
    state_paths = _safe_extra_write_paths(runtime.get("sandbox_state_paths"))
    command_name = Path(command).name.lower()
    home = Path(os.environ.get("HOME") or Path.home()).expanduser().resolve()

    def add_read(path: Path) -> None:
        path = path.expanduser().resolve()
        if path.exists() and path not in read_paths:
            read_paths.append(path)

    def add_state(path: Path, *, boundary: Path | None = None) -> None:
        path = path.expanduser()
        if path.is_symlink():
            raise AgentSandboxError(f"拒绝将符号链接作为 Agent 可写目录：{path}")
        path.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise AgentSandboxError(f"Agent 可写状态路径不是安全目录：{path}")
        resolved = path.resolve(strict=True)
        if boundary is not None:
            resolved_boundary = boundary.resolve(strict=True)
            if resolved != resolved_boundary and resolved_boundary not in resolved.parents:
                raise AgentSandboxError(f"Agent 状态路径越过运行目录：{path}")
        if resolved not in state_paths:
            state_paths.append(resolved)

    if "opencode" in command_name:
        isolated_home = str(runtime.get("sandbox_opencode_home") or "").strip()
        if isolated_home:
            add_state(Path(isolated_home))
        else:
            add_read(home / ".config" / "opencode")
            add_read(home / ".opencode")
            add_state(home / ".local" / "share" / "opencode")
            add_state(home / ".local" / "state" / "opencode")
            add_state(home / ".cache" / "opencode")
    elif "codex" in command_name:
        codex_home = Path(
            runtime.get("sandbox_codex_home")
            or os.environ.get("CODEX_HOME")
            or home / ".codex"
        ).expanduser()
        if codex_home.is_symlink():
            raise AgentSandboxError("拒绝使用符号链接作为 Codex 运行目录。")
        codex_home.mkdir(parents=True, exist_ok=True)
        if not codex_home.is_dir():
            raise AgentSandboxError("Codex 运行目录不可用。")
        codex_home = codex_home.resolve(strict=True)
        add_read(codex_home)
        for state_name in ("sessions", "log", ".tmp", "tmp", "cache"):
            add_state(codex_home / state_name, boundary=codex_home)
        user_skill_roots = [codex_home / "skills"]
        if bool(runtime.get("sandbox_codex_include_user_skills", True)):
            user_skill_roots.append(home / ".agents" / "skills")
        for skill_root in user_skill_roots:
            add_read(skill_root)
        for target in _skill_symlink_targets(user_skill_roots):
            add_read(target)
    elif "claude" in command_name or command_name in {"ccr", "ccr.cmd"}:
        add_state(Path(os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude"))
        ccr_config = str(os.environ.get("CCR_CONFIG_PATH") or "").strip()
        if ccr_config:
            add_read(Path(ccr_config))
    return read_paths, state_paths


def _system_read_paths(platform: str, command: str) -> list[Path]:
    candidates = (
        ["/System", "/usr", "/bin", "/sbin", "/Library", "/opt", "/private/etc", "/private/var/db"]
        if platform.startswith("darwin")
        else ["/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/opt"]
    )
    paths = [Path(value).resolve() for value in candidates if Path(value).exists()]
    if command:
        command_path = Path(command).expanduser()
        if command_path.is_absolute() and command_path.exists():
            command_root = command_path.resolve().parent
            if command_root.name == "bin" and command_root.parent != Path("/"):
                command_root = command_root.parent
            paths.append(command_root)
    return _unique_paths(paths)


def _unique_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved not in result:
            result.append(resolved)
    return result


def _macos_profile(
    *,
    read_paths: list[Path],
    write_paths: list[Path],
    allow_network: bool,
    allowed_network_targets: list[str] | None = None,
) -> str:
    lines = [
        "(version 1)",
        "(deny default)",
        "(allow process*)",
        '(allow file-read* (require-all (require-not (subpath "/Users")) '
        '(require-not (subpath "/Volumes")) '
        '(require-not (subpath "/private/var/folders")) '
        '(require-not (subpath "/private/tmp"))))',
        "(allow file-read-metadata)",
        "(allow sysctl-read)",
        "(allow mach-lookup)",
        "(allow ipc-posix-shm*)",
        "(allow signal (target same-sandbox))",
        '(allow file-write* (literal "/dev/null"))',
    ]
    if allow_network:
        if allowed_network_targets:
            for target in allowed_network_targets:
                lines.append(f'(allow network-outbound (remote ip "{target}"))')
        else:
            lines.append("(allow network-outbound)")
    parent_literals: list[Path] = []
    for path in read_paths:
        parent = path.parent
        while parent != Path("/"):
            if parent not in parent_literals:
                parent_literals.append(parent)
            parent = parent.parent
    for path in sorted(parent_literals, key=lambda item: len(item.parts)):
        lines.append(f'(allow file-read* (literal "{_escape_profile_path(path)}"))')
    for path in read_paths:
        selector = "subpath" if path.is_dir() else "literal"
        lines.append(f'(allow file-read* ({selector} "{_escape_profile_path(path)}"))')
    for path in write_paths:
        lines.append(f'(allow file-write* (subpath "{_escape_profile_path(path)}"))')
    return "\n".join(lines) + "\n"


def _escape_profile_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace('"', '\\"')


def _resolve_approved_proxy_targets(target: str) -> list[str]:
    """Resolve a deployment-owned proxy target before compiling Seatbelt rules.

    The resolved addresses live only in the ephemeral Seatbelt profile.  They
    must never be copied to diagnostics or the persisted policy snapshot.
    """
    host, port = _proxy_target_host_port(target)
    if not host or port is None:
        raise AgentSandboxError("批准代理网关地址无效，无法生成 macOS 网络强制规则。")
    try:
        addresses = sorted({
            item[4][0]
            for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        })
    except OSError as exc:
        raise AgentSandboxError(
            "无法解析批准代理网关，已拒绝启动 Agent。请检查管理员代理 DNS 配置。"
        ) from exc
    if not addresses:
        raise AgentSandboxError(
            "批准代理网关未解析到地址，已拒绝启动 Agent。请检查管理员代理 DNS 配置。"
        )
    # This macOS Seatbelt implementation accepts `localhost:port` for the
    # loopback test case while rejecting textual loopback IPs. Remote gateways
    # use the resolved address form and are never persisted outside the profile.
    if host.lower().rstrip(".") == "localhost":
        return [f"localhost:{port}"]
    return [_seatbelt_remote_address(address, port) for address in addresses]


def _proxy_target_host_port(target: str) -> tuple[str, int | None]:
    value = str(target or "").strip()
    if value.startswith("[") and "]:" in value:
        host, _, port_text = value[1:].partition("]:")
    else:
        host, separator, port_text = value.rpartition(":")
        if not separator:
            return "", None
    try:
        port = int(port_text)
    except ValueError:
        return host, None
    return host.lower().rstrip("."), port if 1 <= port <= 65535 else None


def _seatbelt_remote_address(address: str, port: int) -> str:
    return f"{address}:{port}"
