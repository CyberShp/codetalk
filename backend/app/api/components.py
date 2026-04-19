from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import get_all_adapters
from app.database import get_db
from app.schemas.component_config import (
    ApplyResult,
    ComponentConfigResponse,
    ComponentConfigUpdate,
    ComponentContract,
    ComponentHealth,
    ComponentStatus,
    RestartResult,
)
from app.services import component_manager as cm

router = APIRouter(prefix="/api/components", tags=["components"])


@router.get("/contracts", response_model=list[ComponentContract])
async def get_contracts():
    """Return config contracts for all components."""
    return cm.get_contracts()


@router.get("", response_model=list[ComponentStatus])
async def list_components(db: AsyncSession = Depends(get_db)):
    """List all components with health and config status."""
    contracts = cm.get_contracts()
    adapters = {a.name(): a for a in get_all_adapters()}
    all_configs = await cm.get_all_configs(db)

    statuses = []
    for contract in contracts:
        # Health from adapter
        adapter = adapters.get(contract.component)
        if adapter:
            health = await adapter.health_check()
            comp_health = ComponentHealth(
                component=contract.component,
                healthy=health.is_healthy,
                container_status=health.container_status,
                version=health.version,
            )
        else:
            running, status = await cm.get_container_status(
                contract.component
            )
            comp_health = ComponentHealth(
                component=contract.component,
                healthy=running,
                container_status=status,
            )

        # Config domains
        domains = []
        for cfg in all_configs:
            if cfg.component != contract.component:
                continue
            display = cm.config_to_display(cfg, contract)
            domains.append(
                ComponentConfigResponse(
                    component=cfg.component,
                    domain=cfg.domain,
                    config=display,
                    applied_at=cfg.applied_at,
                    updated_at=cfg.updated_at,
                )
            )

        statuses.append(
            ComponentStatus(
                component=contract.component,
                label=contract.label,
                health=comp_health,
                domains=domains,
            )
        )

    return statuses


@router.put("/{component}/{domain}")
async def save_config(
    component: str,
    domain: str,
    data: ComponentConfigUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Save config to central store (does NOT apply to container)."""
    try:
        cfg = await cm.save_config(db, component, domain, data.config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    contract = cm.get_contract(component)
    if not contract:
        raise HTTPException(status_code=404, detail="Unknown component")

    display = cm.config_to_display(cfg, contract)
    return ComponentConfigResponse(
        component=cfg.component,
        domain=cfg.domain,
        config=display,
        applied_at=cfg.applied_at,
        updated_at=cfg.updated_at,
    )


@router.post("/{component}/apply", response_model=ApplyResult)
async def apply_component_config(
    component: str,
    db: AsyncSession = Depends(get_db),
):
    """Apply saved config: write override file."""
    contract = cm.get_contract(component)
    if not contract:
        raise HTTPException(status_code=404, detail="Unknown component")

    success, message, preview = await cm.apply_config(db, component)
    return ApplyResult(
        success=success,
        message=message,
        override_preview=preview,
    )


@router.post("/{component}/restart", response_model=RestartResult)
async def restart_component(component: str):
    """Restart a component container."""
    if component == "backend":
        return RestartResult(
            success=False,
            message="后端服务无法自行重启",
        )

    contract = cm.get_contract(component)
    if not contract:
        raise HTTPException(status_code=404, detail="Unknown component")

    # Check if there are pending (unapplied) config changes
    # If override exists, use recreate to pick up env changes
    success, message = await cm.restart_container(component)
    return RestartResult(success=success, message=message)


@router.post("/{component}/apply-restart", response_model=RestartResult)
async def apply_and_restart(
    component: str,
    db: AsyncSession = Depends(get_db),
):
    """Apply config + recreate container with new env vars."""
    if component == "backend":
        return RestartResult(
            success=False,
            message="后端服务无法自行重启",
        )

    contract = cm.get_contract(component)
    if not contract:
        raise HTTPException(status_code=404, detail="Unknown component")

    # 1. Resolve env vars from saved config
    configs = await cm.get_configs(db, component)
    env_updates: dict[str, str] = {}
    for cfg in configs:
        env_vars = cm._resolve_env_vars(cfg, contract)
        env_updates.update(env_vars)

    if not env_updates:
        return RestartResult(
            success=False,
            message="没有配置需要应用",
        )

    # 2. Write override file (for compose sync)
    await cm.apply_config(db, component)

    # 3. Recreate container with new env
    success, message = await cm.recreate_container(component, env_updates)
    return RestartResult(success=success, message=message)


@router.get("/{component}/health", response_model=ComponentHealth)
async def component_health(component: str):
    """Check health of a specific component."""
    adapters = {a.name(): a for a in get_all_adapters()}
    adapter = adapters.get(component)

    if adapter:
        health = await adapter.health_check()
        return ComponentHealth(
            component=component,
            healthy=health.is_healthy,
            container_status=health.container_status,
            version=health.version,
        )

    running, status = await cm.get_container_status(component)
    return ComponentHealth(
        component=component,
        healthy=running,
        container_status=status,
    )
