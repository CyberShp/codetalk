"""Static capability snapshot for workflow compilation and trial runs.

The registry answers only whether CodeTalk has a generic handler implementation.
It deliberately does not claim that optional storage-test governance handlers are
available before their runtime implementations are migrated in a later phase.
"""

from __future__ import annotations

from typing import Any


_PHASE1_HANDLERS: dict[str, dict[str, list[int]]] = {
    "agent": {"versions": [1]},
    "artifact_exists": {"versions": [1]},
    "json_schema": {"versions": [1]},
}


def workflow_handler_capability_snapshot() -> dict[str, Any]:
    """Return a detached compiler capability snapshot.

    A returned copy prevents a draft validation from mutating the process-wide
    registry and keeps publish and trial-run decisions on the exact same data.
    """
    return {
        "handlers": {
            handler_id: {"versions": list(spec["versions"])}
            for handler_id, spec in _PHASE1_HANDLERS.items()
        }
    }
