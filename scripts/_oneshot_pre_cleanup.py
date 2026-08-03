from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/app/services/workbench_task_run.py"
text = path.read_text(encoding="utf-8")

replacements = (
    (
        "from app.services.network_policy import IntranetNetworkPolicy\n",
        "",
        "Workbench policy import",
    ),
    (
        '''        network_policy = IntranetNetworkPolicy(\n            policy_id=settings.intranet_network_policy_id,\n            allowed_hosts=set(settings.intranet_allowed_hosts),\n            allowed_cidrs=set(settings.intranet_allowed_cidrs),\n        ).snapshot()\n''',
        "",
        "Workbench policy snapshot construction",
    ),
    (
        '            "network_policy": network_policy,\n',
        "",
        "Workbench task bundle policy field",
    ),
)

for old, new, label in replacements:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one exact match, got {count}")
    text = text.replace(old, new, 1)

for token in (
    "IntranetNetworkPolicy",
    "intranet_network_policy_id",
    "intranet_allowed_hosts",
    "intranet_allowed_cidrs",
    '"network_policy": network_policy',
):
    if token in text:
        raise RuntimeError(f"Workbench legacy policy token remains: {token}")

path.write_text(text, encoding="utf-8")
Path(__file__).unlink(missing_ok=True)
print("removed Workbench network-policy import, snapshot construction, and task-bundle field")
