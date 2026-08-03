from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/app/services/workbench_task_run.py"
lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
tokens = {"intranet_allowed_hosts", "intranet_allowed_cidrs"}
removed: list[str] = []
result: list[str] = []

for index, line in enumerate(lines, start=1):
    matched = [token for token in tokens if token in line]
    if not matched:
        result.append(line)
        continue
    if len(matched) != 1:
        raise RuntimeError(f"ambiguous legacy policy line {index}: {line.rstrip()}")
    token = matched[0]
    if re.fullmatch(rf"\s*[\"']{re.escape(token)}[\"']\s*,?\s*\n?", line):
        removed.append(f"{index}:{line.rstrip()}")
        continue
    context = "".join(lines[max(0, index - 4): min(len(lines), index + 3)])
    raise RuntimeError(
        f"legacy policy token is not a standalone collection item at line {index}:\n{context}"
    )

if len(removed) != 2:
    raise RuntimeError(f"expected to remove two legacy policy keys, removed={removed}")

path.write_text("".join(result), encoding="utf-8")
print("removed legacy Workbench policy snapshot keys:")
print("\n".join(removed))
