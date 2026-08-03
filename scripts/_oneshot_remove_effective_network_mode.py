from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/app/config.py"
text = path.read_text(encoding="utf-8")
old = '''    @property
    def effective_network_mode(self) -> Literal["developer", "intranet", "strict_compliance"]:
        """Resolve the new deployment mode without rewriting legacy settings."""
        if self.network_mode is not None:
            return self.network_mode
        return "intranet" if self.intranet_network_mode else "developer"

'''
if text.count(old) != 1:
    raise RuntimeError(f"effective_network_mode compatibility property matches={text.count(old)}")
text = text.replace(old, "", 1)
for token in ("effective_network_mode", "network_mode", "intranet_network_mode"):
    if token in text:
        raise RuntimeError(f"network mode compatibility remains in config.py: {token}")
path.write_text(text, encoding="utf-8")
Path(__file__).unlink(missing_ok=True)
print("removed effective network mode compatibility property")
