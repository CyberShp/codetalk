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
    write_paths = [artifact_dir, *extra_write_paths]
    base_audit = {
        "version": "agent-sandbox-policy-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "platform": platform,
        "workspace": str(workspace or ""),
        "workspace_access": "read_only",
        "artifact_dir": str(artifact_dir),
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
                    workspace=workspace,
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
            wrapper = [bwrap, "--die-with-parent", "--new-session", "--ro-bind", "/", "/"]
            for path in write_paths:
                wrapper.extend(["--bind", str(path), str(path)])
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


def _macos_profile(
    *,
    workspace: Path | None,
    write_paths: list[Path],
    allow_network: bool,
) -> str:
    lines = [
        "(version 1)",
        "(deny default)",
        "(allow process*)",
        "(allow file-read*)",
        "(allow sysctl-read)",
        "(allow mach-lookup)",
        "(allow signal (target same-sandbox))",
        '(allow file-write* (literal "/dev/null"))',
    ]
    if allow_network:
        lines.append("(allow network-outbound)")
    for path in write_paths:
        lines.append(f'(allow file-write* (subpath "{_escape_profile_path(path)}"))')
    if workspace:
        lines.append(f'; workspace read-only: "{_escape_profile_path(workspace)}"')
    return "\n".join(lines) + "\n"


def _escape_profile_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace('"', '\\"')
