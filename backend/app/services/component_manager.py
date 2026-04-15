"""Component configuration manager.

Responsibilities:
  - Define config contracts (what env vars each component needs)
  - Generate docker-compose.override.yml
  - Restart containers via Docker Engine API (Unix socket + httpx)
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.component_config import ComponentConfig
from app.schemas.component_config import (
    ComponentContract,
    ConfigDomain,
    ConfigField,
)
from app.utils.crypto import decrypt_key, encrypt_key

logger = logging.getLogger(__name__)

DOCKER_SOCKET = "/var/run/docker.sock"
PROJECT_DIR = Path("/project")
COMPOSE_PROJECT = "codetalk"

# ── Config contracts: what each component accepts ──────────────────

CONTRACTS: dict[str, ComponentContract] = {
    "deepwiki": ComponentContract(
        component="deepwiki",
        label="DeepWiki (文档引擎)",
        domains=[
            ConfigDomain(
                domain="chat",
                label="Chat 模型",
                env_map={
                    "base_url": "OPENAI_BASE_URL",
                    "api_key": "OPENAI_API_KEY",
                },
                fields=[
                    ConfigField(
                        name="base_url",
                        label="Base URL",
                        field_type="url",
                        placeholder="https://api.openai.com/v1",
                    ),
                    ConfigField(
                        name="api_key",
                        label="API Key",
                        field_type="secret",
                        placeholder="sk-...",
                    ),
                ],
            ),
            ConfigDomain(
                domain="embedding",
                label="Embedding 模型",
                env_map={
                    "base_url": "DEEPWIKI_EMBEDDING_BASE_URL",
                    "api_key": "DEEPWIKI_EMBEDDING_API_KEY",
                    "embedder_type": "DEEPWIKI_EMBEDDER_TYPE",
                },
                fields=[
                    ConfigField(
                        name="base_url",
                        label="Base URL",
                        field_type="url",
                        placeholder="https://api.openai.com/v1",
                    ),
                    ConfigField(
                        name="api_key",
                        label="API Key",
                        field_type="secret",
                        placeholder="sk-...",
                    ),
                    ConfigField(
                        name="embedder_type",
                        label="Embedder 类型",
                        field_type="select",
                        options=["openai", "ollama", "google", "bedrock"],
                    ),
                ],
            ),
        ],
    ),
    "gitnexus": ComponentContract(
        component="gitnexus",
        label="GitNexus (代码图谱)",
        domains=[],
    ),
    "zoekt": ComponentContract(
        component="zoekt",
        label="Zoekt (代码搜索)",
        domains=[],
    ),
}


def get_contracts() -> list[ComponentContract]:
    return list(CONTRACTS.values())


def get_contract(component: str) -> ComponentContract | None:
    return CONTRACTS.get(component)


# ── Config persistence (DB + encryption) ───────────────────────────


async def save_config(
    db: AsyncSession,
    component: str,
    domain: str,
    config: dict[str, str],
) -> ComponentConfig:
    """Save component config to DB. Encrypts secret fields."""
    contract = CONTRACTS.get(component)
    if not contract:
        raise ValueError(f"Unknown component: {component}")

    domain_contract = next(
        (d for d in contract.domains if d.domain == domain), None
    )
    if not domain_contract:
        raise ValueError(f"Unknown domain {domain} for {component}")

    # Encrypt secret fields
    stored = {}
    secret_fields = {
        f.name for f in domain_contract.fields if f.field_type == "secret"
    }
    for key, value in config.items():
        if key in secret_fields and value:
            stored[key] = encrypt_key(value)
        else:
            stored[key] = value

    # Upsert
    result = await db.execute(
        select(ComponentConfig).where(
            ComponentConfig.component == component,
            ComponentConfig.domain == domain,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        # Merge: keep old encrypted values if new value is empty
        for key in secret_fields:
            if not config.get(key) and key in existing.config:
                stored[key] = existing.config[key]
        existing.config = stored
        existing.updated_at = datetime.now(timezone.utc)
    else:
        existing = ComponentConfig(
            component=component,
            domain=domain,
            config=stored,
        )
        db.add(existing)

    await db.commit()
    await db.refresh(existing)
    return existing


async def get_configs(
    db: AsyncSession, component: str
) -> list[ComponentConfig]:
    result = await db.execute(
        select(ComponentConfig).where(
            ComponentConfig.component == component
        )
    )
    return list(result.scalars().all())


async def get_all_configs(db: AsyncSession) -> list[ComponentConfig]:
    result = await db.execute(select(ComponentConfig))
    return list(result.scalars().all())


def config_to_display(
    cfg: ComponentConfig, contract: ComponentContract
) -> dict[str, str]:
    """Convert stored config to display format (mask secrets)."""
    domain_contract = next(
        (d for d in contract.domains if d.domain == cfg.domain), None
    )
    if not domain_contract:
        return cfg.config

    secret_fields = {
        f.name for f in domain_contract.fields if f.field_type == "secret"
    }
    display = {}
    for key, value in cfg.config.items():
        if key in secret_fields and value:
            display[key] = "••••••••"
        else:
            display[key] = value
    return display


# ── Override generation ────────────────────────────────────────────


def _resolve_env_vars(
    cfg: ComponentConfig, contract: ComponentContract
) -> dict[str, str]:
    """Map config values to Docker env var names using contract."""
    domain_contract = next(
        (d for d in contract.domains if d.domain == cfg.domain), None
    )
    if not domain_contract:
        return {}

    env_vars = {}
    secret_fields = {
        f.name for f in domain_contract.fields if f.field_type == "secret"
    }

    for config_key, env_name in domain_contract.env_map.items():
        value = cfg.config.get(config_key, "")
        if not value:
            continue
        # Decrypt secrets
        if config_key in secret_fields:
            value = decrypt_key(value)
        env_vars[env_name] = value

    return env_vars


def generate_override(
    configs: list[ComponentConfig],
) -> tuple[str, dict[str, dict[str, str]]]:
    """Generate docker-compose.override.yml content.

    Returns (yaml_content, preview_dict) where preview masks secrets.
    """
    services: dict[str, dict[str, str]] = {}
    preview: dict[str, dict[str, str]] = {}

    for cfg in configs:
        contract = CONTRACTS.get(cfg.component)
        if not contract:
            continue

        env_vars = _resolve_env_vars(cfg, contract)
        if not env_vars:
            continue

        svc = services.setdefault(cfg.component, {})
        svc.update(env_vars)

        # Preview: mask API keys
        pv = preview.setdefault(cfg.component, {})
        for k, v in env_vars.items():
            if "KEY" in k.upper() or "SECRET" in k.upper():
                pv[k] = v[:4] + "••••" if len(v) > 4 else "••••"
            else:
                pv[k] = v

    if not services:
        return "", {}

    lines = [
        "# Auto-generated by CodeTalks. Do not edit manually.",
        "services:",
    ]
    for service, env_vars in services.items():
        lines.append(f"  {service}:")
        lines.append("    environment:")
        for key, value in env_vars.items():
            escaped = value.replace('"', '\\"')
            lines.append(f'      {key}: "{escaped}"')

    return "\n".join(lines) + "\n", preview


async def apply_config(
    db: AsyncSession, component: str
) -> tuple[bool, str, dict[str, str] | None]:
    """Apply configs: write override file and mark as applied."""
    all_configs = await get_all_configs(db)
    yaml_content, preview = generate_override(all_configs)

    if not yaml_content:
        return False, "没有可应用的配置", None

    override_path = PROJECT_DIR / "docker-compose.override.yml"
    try:
        override_path.write_text(yaml_content)
    except OSError as exc:
        return False, f"写入 override 文件失败: {exc}", None

    # Mark target component configs as applied
    for cfg in all_configs:
        if cfg.component == component:
            cfg.applied_at = datetime.now(timezone.utc)
    await db.commit()

    component_preview = preview.get(component, {})
    return True, "配置已写入 override 文件", component_preview


# ── Docker Engine API (via Unix socket) ────────────────────────────


def _docker_client() -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(uds=DOCKER_SOCKET)
    return httpx.AsyncClient(
        transport=transport,
        base_url="http://localhost",
        timeout=httpx.Timeout(60, connect=10),
    )


def _container_name(component: str) -> str:
    return f"{COMPOSE_PROJECT}-{component}-1"


async def restart_container(component: str) -> tuple[bool, str]:
    """Restart a container via Docker Engine API."""
    name = _container_name(component)
    try:
        async with _docker_client() as client:
            # Check container exists
            resp = await client.get(f"/containers/{name}/json")
            if resp.status_code == 404:
                return False, f"容器 {name} 不存在"

            # Restart (stop + start, re-reads env_file if configured)
            resp = await client.post(
                f"/containers/{name}/restart", params={"t": "30"}
            )
            if resp.status_code == 204:
                return True, f"容器 {name} 已重启"
            return False, f"重启失败: HTTP {resp.status_code}"
    except httpx.ConnectError:
        return False, "无法连接 Docker — 请确认 Docker socket 已挂载"
    except Exception as exc:
        return False, f"重启异常: {exc}"


async def recreate_container(
    component: str, env_updates: dict[str, str]
) -> tuple[bool, str]:
    """Recreate container with updated env vars via Docker Engine API."""
    name = _container_name(component)
    try:
        async with _docker_client() as client:
            # 1. Inspect current container
            resp = await client.get(f"/containers/{name}/json")
            if resp.status_code == 404:
                return False, f"容器 {name} 不存在"
            attrs = resp.json()

            config = attrs["Config"]
            host_config = attrs["HostConfig"]
            networks = attrs["NetworkSettings"]["Networks"]

            # 2. Merge env vars
            old_env = {}
            for entry in config.get("Env", []):
                k, _, v = entry.partition("=")
                old_env[k] = v
            old_env.update(env_updates)

            # 3. Stop + remove
            await client.post(
                f"/containers/{name}/stop", params={"t": "30"}
            )
            await client.delete(f"/containers/{name}")

            # 4. Build NetworkingConfig with DNS aliases
            #    NetworkMode auto-connects during creation, so aliases
            #    must be set here (a separate /connect is a no-op).
            endpoints_config: dict = {}
            for net_name, net_cfg in networks.items():
                if net_name == "bridge":
                    continue
                aliases = list(net_cfg.get("Aliases") or [])
                if component not in aliases:
                    aliases.append(component)
                endpoints_config[net_name] = {"Aliases": aliases}

            create_body = {
                "Image": config["Image"],
                "Cmd": config.get("Cmd"),
                "Entrypoint": config.get("Entrypoint"),
                "Env": [f"{k}={v}" for k, v in old_env.items()],
                "Labels": config.get("Labels", {}),
                "ExposedPorts": config.get("ExposedPorts", {}),
                "HostConfig": {
                    "Binds": host_config.get("Binds", []),
                    "PortBindings": host_config.get("PortBindings", {}),
                    "NetworkMode": host_config.get(
                        "NetworkMode", "default"
                    ),
                    "RestartPolicy": host_config.get(
                        "RestartPolicy", {}
                    ),
                },
                "NetworkingConfig": {
                    "EndpointsConfig": endpoints_config,
                },
            }
            healthcheck = config.get("Healthcheck")
            if healthcheck:
                create_body["Healthcheck"] = healthcheck

            resp = await client.post(
                "/containers/create",
                params={"name": name},
                json=create_body,
            )
            if resp.status_code not in (200, 201):
                return False, f"创建容器失败: {resp.text}"

            container_id = resp.json()["Id"]

            # 6. Start
            resp = await client.post(
                f"/containers/{container_id}/start"
            )
            if resp.status_code == 204:
                return True, f"容器 {name} 已重建并启动"
            return False, f"启动失败: HTTP {resp.status_code}"
    except httpx.ConnectError:
        return False, "无法连接 Docker — 请确认 Docker socket 已挂载"
    except Exception as exc:
        return False, f"重建容器异常: {exc}"


async def get_container_status(
    component: str,
) -> tuple[bool, str]:
    """Check container status via Docker Engine API."""
    name = _container_name(component)
    try:
        async with _docker_client() as client:
            resp = await client.get(f"/containers/{name}/json")
            if resp.status_code == 404:
                return False, "not_found"
            state = resp.json().get("State", {})
            running = state.get("Running", False)
            status = state.get("Status", "unknown")
            return running, status
    except httpx.ConnectError:
        return False, "docker_unavailable"
    except Exception:
        return False, "error"
