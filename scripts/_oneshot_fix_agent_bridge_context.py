from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/app/services/agent_cli_bridge.py"
text = path.read_text(encoding="utf-8")
old = "        context=network_context,\n"
if text.count(old) != 1:
    raise RuntimeError(f"stale stream Agent network context matches={text.count(old)}")
text = text.replace(old, "", 1)
if "network_context" in text:
    raise RuntimeError("additional network_context residue remains in agent_cli_bridge.py")
path.write_text(text, encoding="utf-8")
Path(__file__).unlink(missing_ok=True)
print("removed stale stream Agent network_context argument")
