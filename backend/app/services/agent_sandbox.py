from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class AgentSandboxError(RuntimeError):
    pass


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
        return _unavailable(artifact_dir, mode=mode, audit=base_audit, engine="sandbox-exec")
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
        return _unavailable(artifact_dir, mode=mode, audit=base_audit, engine="bubblewrap")
    return _unavailable(
        artifact_dir,
        mode=mode,
        audit=base_audit,
        engine="unsupported_platform",
    )


def _unavailable(
    artifact_dir: Path,
    *,
    mode: str,
    audit: dict[str, Any],
    engine: str,
) -> AgentSandboxLaunch:
    message = (
        f"当前系统不支持所需 Agent OS 隔离（缺少 {engine}）。"
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
    home = Path.home().resolve()

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
        user_skill_roots = [codex_home / "skills", home / ".agents" / "skills"]
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
