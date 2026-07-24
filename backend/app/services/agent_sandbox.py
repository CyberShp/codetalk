from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class AgentSandboxError(RuntimeError):
    pass


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


_ISOLATED_RUNTIME_PREFIXES = (".runtime-tmp-", ".runtime-codex-home-")


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
    mode = str(runtime.get("sandbox_mode") or "auto").strip().lower()
    if mode not in {"auto", "required", "off"}:
        raise AgentSandboxError(f"未知 Agent 隔离模式：{mode}")
    # A deployment that permits an Agent to reach an approved model endpoint
    # still needs an OS boundary.  Environment scrubbing and a deployment
    # firewall are complementary controls, not a justification for an
    # unsandboxed subprocess fallback.
    intranet_requires_os_sandbox = bool(runtime.get("intranet_require_os_sandbox"))
    if intranet_requires_os_sandbox and mode == "off":
        raise AgentSandboxError(
            "内网 Agent 运行需要 OS 隔离，不能将 sandbox_mode 设为 off。"
        )
    if intranet_requires_os_sandbox:
        mode = "required"
    platform = str(platform_name or sys.platform).lower()
    artifact_dir = artifact_dir.resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    workspace = Path(cwd).resolve() if cwd else None
    allow_network = bool(runtime.get("sandbox_allow_network", True))
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
        "mode": mode,
        "platform": platform,
        "workspace": str(workspace or ""),
        "workspace_access": "read_only",
        "read_boundary": "system_runtime_plus_declared_workspace_and_provider_state",
        "artifact_dir": str(artifact_dir),
        "read_paths": [str(path) for path in read_paths],
        "runtime_state_paths": [str(path) for path in runtime_state_paths],
        "write_paths": [str(path) for path in write_paths],
        "network": "outbound_allowed" if allow_network else "blocked",
        "subprocess": "allowed_and_inherited",
        "environment": "allowlisted_parent_plus_runtime_explicit",
    }
    if mode == "off":
        return _persist_launch(
            artifact_dir,
            status="disabled",
            wrapper=[],
            message="Agent OS 隔离已由配置关闭。",
            audit=base_audit,
        )
    if platform.startswith("darwin"):
        sandbox_exec = which("sandbox-exec")
        if sandbox_exec:
            profile_path = artifact_dir / "sandbox-profile.sb"
            profile_path.write_text(
                _macos_profile(
                    read_paths=read_paths,
                    write_paths=write_paths,
                    allow_network=allow_network,
                ),
                encoding="utf-8",
            )
            return _persist_launch(
                artifact_dir,
                status="active",
                wrapper=[sandbox_exec, "-f", str(profile_path)],
                message="已启用 macOS sandbox-exec 隔离。",
                audit={**base_audit, "engine": "sandbox-exec", "profile": str(profile_path)},
            )
        return _unavailable(
            artifact_dir,
            mode=mode,
            audit=base_audit,
            engine="sandbox-exec",
            required_reason=("内网 Agent 运行需要 OS 隔离。" if intranet_requires_os_sandbox else ""),
        )
    if platform.startswith("linux"):
        bwrap = which("bwrap") or which("bubblewrap")
        if bwrap:
            wrapper = [bwrap, "--die-with-parent", "--new-session", "--tmpfs", "/"]
            for path in read_paths:
                if path in write_paths:
                    continue
                wrapper.extend(["--ro-bind", str(path), str(path)])
            for path in write_paths:
                wrapper.extend(["--bind", str(path), str(path)])
            wrapper.extend(["--dev", "/dev", "--proc", "/proc"])
            if workspace:
                wrapper.extend(["--chdir", str(workspace)])
            if not allow_network:
                wrapper.append("--unshare-net")
            return _persist_launch(
                artifact_dir,
                status="active",
                wrapper=wrapper,
                message="已启用 Linux bubblewrap 隔离。",
                audit={**base_audit, "engine": "bubblewrap"},
            )
        return _unavailable(
            artifact_dir,
            mode=mode,
            audit=base_audit,
            engine="bubblewrap",
            required_reason=("内网 Agent 运行需要 OS 隔离。" if intranet_requires_os_sandbox else ""),
        )
    return _unavailable(
        artifact_dir,
        mode=mode,
        audit=base_audit,
        engine="unsupported_platform",
        required_reason=("内网 Agent 运行需要 OS 隔离。" if intranet_requires_os_sandbox else ""),
    )


def _unavailable(
    artifact_dir: Path,
    *,
    mode: str,
    audit: dict[str, Any],
    engine: str,
    required_reason: str = "",
) -> AgentSandboxLaunch:
    message = (
        f"{required_reason}当前系统不支持所需 Agent OS 隔离（缺少 {engine}）。"
        "请安装隔离工具，或由管理员将隔离模式改为 auto 后以降级模式运行。"
    )
    rejected = {**audit, "status": "rejected" if mode == "required" else "degraded", "engine": engine, "message": message}
    _write_audit(artifact_dir, rejected)
    if mode == "required":
        raise AgentSandboxError(message)
    return AgentSandboxLaunch(
        status="degraded",
        wrapper=[],
        message=message,
        audit=rejected,
    )


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
        lines.append("(allow network-outbound)")
    parent_literals: list[Path] = []
    for path in read_paths:
        if not str(path).startswith("/Users/"):
            continue
        parent = path.parent
        while str(parent).startswith("/Users") and parent != Path("/"):
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
