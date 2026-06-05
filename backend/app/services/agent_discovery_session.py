"""Task-scoped context control for external-agent discovery.

The session owns CodeTalk's memory.  External agents can be short-lived
processes; every round receives a structured context packet derived from this
ledger, never from a free-form agent summary.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from app.config import settings
from app.services.external_agent_discovery import validate_agent_candidate_file

SessionGoal = Literal["workspace_scope", "coverage_entry", "mixed"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SourceSliceRef:
    slice_id: str
    file_path: str
    start_line: int | None = None
    end_line: int | None = None
    symbol: str | None = None
    reason: str = ""
    sha256: str = ""
    excerpt: str = ""
    validated: bool = False
    validation_error: str | None = None


@dataclass
class AgentContextPacketInput:
    object_id: str
    current_goal: str
    analysis_object_text: str
    expanded_terms: list[str] = field(default_factory=list)
    path_hints: list[str] = field(default_factory=list)
    scope_hints: list[dict] = field(default_factory=list)
    coverage_hit: dict | None = None
    existing_tool_candidates: list[dict] = field(default_factory=list)
    round_index: int = 1


@dataclass
class AgentDiscoveryLedger:
    expanded_terms_by_object: dict[str, list[str]] = field(default_factory=dict)
    local_candidates: list[dict] = field(default_factory=list)
    gitnexus_candidates: list[dict] = field(default_factory=list)
    cgc_candidates: list[dict] = field(default_factory=list)
    validated_files: list[dict] = field(default_factory=list)
    rejected_files: list[dict] = field(default_factory=list)
    validated_symbols: list[dict] = field(default_factory=list)
    validated_entries: list[dict] = field(default_factory=list)
    rejected_entries: list[dict] = field(default_factory=list)
    provider_status: dict[str, str] = field(default_factory=dict)
    command_history: list[dict] = field(default_factory=list)
    source_slices: list[dict] = field(default_factory=list)
    unresolved_items: list[dict] = field(default_factory=list)

    def add_validated_file(
        self,
        *,
        object_id: str,
        path: str,
        provider: str,
        reason: str,
        confidence: str = "medium",
    ) -> None:
        self._append_unique(self.validated_files, {
            "object_id": object_id,
            "path": path,
            "provider": provider,
            "reason": reason,
            "confidence": confidence,
            "created_at": _now(),
        }, key_fields=("object_id", "path", "provider"))

    def add_rejected_file(
        self,
        *,
        object_id: str,
        path: str,
        provider: str,
        reason: str,
    ) -> None:
        self._append_unique(self.rejected_files, {
            "object_id": object_id,
            "path": path,
            "provider": provider,
            "reason": reason,
            "created_at": _now(),
        }, key_fields=("object_id", "path", "provider", "reason"))

    def add_validated_entry(self, entry: dict) -> None:
        self._append_unique(
            self.validated_entries,
            {**entry, "created_at": entry.get("created_at") or _now()},
            key_fields=("entry_symbol", "entry_file", "provider"),
        )

    def add_rejected_entry(self, entry: dict) -> None:
        self._append_unique(
            self.rejected_entries,
            {**entry, "created_at": entry.get("created_at") or _now()},
            key_fields=("entry_symbol", "entry_file", "provider", "validation_error"),
        )

    @staticmethod
    def _append_unique(target: list[dict], item: dict, *, key_fields: tuple[str, ...]) -> None:
        key = tuple(str(item.get(field) or "") for field in key_fields)
        for existing in target:
            if tuple(str(existing.get(field) or "") for field in key_fields) == key:
                existing.update(item)
                return
        target.append(item)


@dataclass
class AgentDiscoveryTurn:
    turn_id: str
    provider: str
    goal: str
    prompt_path: str | None
    raw_output_path: str | None
    parsed_result: dict
    validation_result: dict
    status: str
    started_at: str
    finished_at: str


@dataclass
class AgentDiscoverySession:
    session_id: str
    repo_path: str
    goal: SessionGoal
    artifact_dir: Path
    task_id: str | None = None
    coverage_analysis_id: str | None = None
    workspace_id: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    objects: list[dict] = field(default_factory=list)
    turns: list[AgentDiscoveryTurn] = field(default_factory=list)
    ledger: AgentDiscoveryLedger = field(default_factory=AgentDiscoveryLedger)

    def save(self) -> None:
        self.updated_at = _now()
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        session_payload = self.to_dict(include_ledger=False)
        ledger_payload = asdict(self.ledger)
        (self.artifact_dir / "agent_discovery_session.json").write_text(
            json.dumps(session_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.artifact_dir / "agent_discovery_ledger.json").write_text(
            json.dumps(ledger_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def to_dict(self, *, include_ledger: bool = True) -> dict:
        payload = {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "coverage_analysis_id": self.coverage_analysis_id,
            "workspace_id": self.workspace_id,
            "repo_path": self.repo_path,
            "goal": self.goal,
            "artifact_dir": str(self.artifact_dir),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "objects": self.objects,
            "turns": [asdict(turn) for turn in self.turns],
        }
        if include_ledger:
            payload["ledger"] = asdict(self.ledger)
        return payload

    @classmethod
    def load(cls, artifact_dir: str | Path) -> "AgentDiscoverySession":
        root = Path(artifact_dir)
        session_data = json.loads(
            (root / "agent_discovery_session.json").read_text(encoding="utf-8")
        )
        ledger_data = json.loads(
            (root / "agent_discovery_ledger.json").read_text(encoding="utf-8")
        )
        session = cls(
            session_id=session_data["session_id"],
            task_id=session_data.get("task_id"),
            coverage_analysis_id=session_data.get("coverage_analysis_id"),
            workspace_id=session_data.get("workspace_id"),
            repo_path=session_data["repo_path"],
            goal=session_data["goal"],
            artifact_dir=root,
            created_at=session_data.get("created_at") or _now(),
            updated_at=session_data.get("updated_at") or _now(),
            objects=session_data.get("objects") or [],
            turns=[
                AgentDiscoveryTurn(**turn)
                for turn in session_data.get("turns") or []
            ],
            ledger=AgentDiscoveryLedger(**ledger_data),
        )
        return session

    def record_turn(
        self,
        *,
        provider: str,
        goal: str,
        prompt: str,
        raw_output: str,
        parsed_result: dict,
        validation_result: dict,
        status: str,
    ) -> AgentDiscoveryTurn:
        turn_id = f"turn_{len(self.turns) + 1:03d}_{provider.replace('-', '_')}"
        started_at = _now()
        prompt_path: str | None = None
        raw_path: str | None = None
        if settings.agent_discovery_store_prompts:
            prompt_dir = self.artifact_dir / "external_agent_prompts"
            prompt_dir.mkdir(parents=True, exist_ok=True)
            prompt_file = prompt_dir / f"{turn_id}.{provider}.json"
            prompt_file.write_text(
                json.dumps({"prompt": prompt}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            prompt_path = str(prompt_file)
        if settings.agent_discovery_store_raw_outputs:
            raw_dir = self.artifact_dir / "external_agent_raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            raw_file = raw_dir / f"{turn_id}.{provider}.txt"
            raw_file.write_text(raw_output or "", encoding="utf-8")
            raw_path = str(raw_file)
        turn = AgentDiscoveryTurn(
            turn_id=turn_id,
            provider=provider,
            goal=goal,
            prompt_path=prompt_path,
            raw_output_path=raw_path,
            parsed_result=parsed_result,
            validation_result=validation_result,
            status=status,
            started_at=started_at,
            finished_at=_now(),
        )
        self.turns.append(turn)
        self.ledger.provider_status[provider] = status
        self.save()
        return turn

    def add_source_slice(
        self,
        path: str,
        *,
        symbol: str | None,
        reason: str,
        object_id: str = "",
    ) -> SourceSliceRef:
        validation = validate_agent_candidate_file(self.repo_path, path)
        if not validation.validated or not validation.resolved_path or not validation.path:
            self.ledger.add_rejected_file(
                object_id=object_id,
                path=path,
                provider="source_slice",
                reason=validation.validation_error or "invalid_source_slice",
            )
            self.save()
            return SourceSliceRef(
                slice_id=f"slice_{len(self.ledger.source_slices) + 1:03d}",
                file_path=validation.path or path,
                symbol=symbol,
                reason=reason,
                validated=False,
                validation_error=validation.validation_error or "invalid_source_slice",
            )

        source_path = Path(validation.resolved_path)
        text = source_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        center = _find_symbol_line(lines, symbol) if symbol else 1
        half = max(1, settings.agent_discovery_source_slice_lines // 2)
        start = max(1, center - half)
        end = min(len(lines), start + settings.agent_discovery_source_slice_lines - 1)
        excerpt = "\n".join(lines[start - 1:end])
        digest = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        slice_id = f"slice_{len(self.ledger.source_slices) + 1:03d}"
        ref = SourceSliceRef(
            slice_id=slice_id,
            file_path=validation.path,
            start_line=start,
            end_line=end,
            symbol=symbol,
            reason=reason,
            sha256=digest,
            excerpt=excerpt,
            validated=True,
        )
        self.ledger.source_slices.append(asdict(ref))
        if settings.agent_discovery_store_source_slices:
            slice_dir = self.artifact_dir / "external_agent_source_slices"
            slice_dir.mkdir(parents=True, exist_ok=True)
            (slice_dir / f"{slice_id}.json").write_text(
                json.dumps(asdict(ref), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        self.save()
        return ref

    def add_source_slices_from_requests(
        self,
        requests: list[dict],
        *,
        object_id: str = "",
    ) -> list[SourceSliceRef]:
        refs: list[SourceSliceRef] = []
        remaining = max(0, settings.agent_discovery_max_source_slices - len(self.ledger.source_slices))
        for item in requests[:remaining]:
            if not isinstance(item, dict):
                continue
            refs.append(self.add_source_slice(
                str(item.get("file_path") or item.get("path") or ""),
                symbol=str(item.get("symbol") or "") or None,
                reason=str(item.get("reason") or "agent requested source slice"),
                object_id=object_id,
            ))
        return refs

    def build_context_packet(self, data: AgentContextPacketInput) -> dict:
        self.ledger.expanded_terms_by_object[data.object_id] = data.expanded_terms
        packet_id = f"packet_{len(list((self.artifact_dir / 'external_agent_context_packets').glob('*.json'))) + 1:03d}"
        packet = {
            "packet_id": packet_id,
            "session_id": self.session_id,
            "repo_path": str(Path(self.repo_path).resolve()),
            "current_goal": data.current_goal,
            "current_object": {
                "object_id": data.object_id,
                "analysis_object_text": data.analysis_object_text,
                "path_hints": data.path_hints,
                "scope_hints": data.scope_hints,
                "coverage_hit": data.coverage_hit,
                "round_index": data.round_index,
            },
            "expanded_terms": data.expanded_terms,
            "validated_facts": {
                "files": _filter_by_object(self.ledger.validated_files, data.object_id),
                "symbols": _filter_by_object(self.ledger.validated_symbols, data.object_id),
                "entries": _filter_by_object(self.ledger.validated_entries, data.object_id),
            },
            "rejected_facts": {
                "files": _filter_by_object(self.ledger.rejected_files, data.object_id),
                "entries": _filter_by_object(self.ledger.rejected_entries, data.object_id),
            },
            "existing_tool_candidates": data.existing_tool_candidates,
            "relevant_source_slices": self.ledger.source_slices[-settings.agent_discovery_max_source_slices:],
            "previous_agent_findings": _verified_agent_findings(self.ledger, data.object_id),
            "do_not_repeat": {
                "paths": sorted({
                    str(item.get("path") or "")
                    for item in self.ledger.rejected_files
                    if not data.object_id or item.get("object_id") in {"", data.object_id}
                } - {""}),
            },
            "requested_output_schema": _agent_output_schema(),
            "context_overflow": {
                "overflow": False,
                "policy": "request_more_in_next_round",
                "dropped_sections": [],
            },
        }
        packet = _enforce_packet_budget(packet)
        packet_dir = self.artifact_dir / "external_agent_context_packets"
        packet_dir.mkdir(parents=True, exist_ok=True)
        (packet_dir / f"{packet_id}.json").write_text(
            json.dumps(packet, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.save()
        return packet


def _find_symbol_line(lines: list[str], symbol: str | None) -> int:
    if not symbol:
        return 1
    for idx, line in enumerate(lines, start=1):
        if symbol in line:
            return idx
    return 1


def _filter_by_object(items: list[dict], object_id: str) -> list[dict]:
    return [
        item for item in items
        if not object_id or item.get("object_id") in {"", object_id}
    ]


def _verified_agent_findings(ledger: AgentDiscoveryLedger, object_id: str) -> list[dict]:
    findings: list[dict] = []
    for item in _filter_by_object(ledger.validated_files, object_id):
        if item.get("provider") not in {"local", "gitnexus", "cgc"}:
            findings.append({
                "kind": "file",
                "path": item.get("path"),
                "provider": item.get("provider"),
                "reason": item.get("reason"),
            })
    for item in _filter_by_object(ledger.validated_entries, object_id):
        findings.append({
            "kind": "entry",
            "entry_symbol": item.get("entry_symbol"),
            "entry_file": item.get("entry_file"),
            "provider": item.get("provider"),
            "reason": item.get("reason"),
        })
    return findings


def _enforce_packet_budget(packet: dict) -> dict:
    max_chars = max(100, settings.agent_discovery_context_packet_max_chars)
    encoded = json.dumps(packet, ensure_ascii=False)
    if len(encoded) <= max_chars:
        return packet
    packet["context_overflow"] = {
        "overflow": True,
        "policy": "request_more_in_next_round",
        "dropped_sections": ["relevant_source_slices", "existing_tool_candidates"],
    }
    packet["relevant_source_slices"] = []
    packet["existing_tool_candidates"] = packet.get("existing_tool_candidates", [])[:3]
    encoded = json.dumps(packet, ensure_ascii=False)
    if len(encoded) > max_chars:
        packet["validated_facts"]["files"] = packet["validated_facts"].get("files", [])[:5]
        packet["rejected_facts"]["files"] = packet["rejected_facts"].get("files", [])[:10]
        packet["previous_agent_findings"] = packet.get("previous_agent_findings", [])[:5]
    return packet


def _agent_output_schema() -> dict:
    return {
        "candidate_files": [
            {
                "path": "repo/relative/source.c",
                "reason": "why this is relevant",
                "confidence": "high|medium|low",
                "evidence_excerpt": "short excerpt",
            }
        ],
        "candidate_symbols": [{"symbol": "name", "file": "repo/relative/source.c", "reason": "..."}],
        "candidate_entries": [
            {
                "entry_kind": "rpc|api|cli|config|message|timer|callback|external",
                "entry_symbol": "public_entry",
                "entry_file": "repo/relative/source.c",
                "chain": ["public_entry", "target_function"],
                "external_trigger": "how a user/test can trigger it",
                "reason": "source-backed reasoning",
            }
        ],
        "need_source_slices": [
            {
                "file_path": "repo/relative/source.c",
                "symbol": "symbol_or_empty",
                "reason": "why more source is needed",
            }
        ],
        "commands": ["rg --files"],
        "raw_summary": "short non-authoritative summary",
        "warnings": [],
    }


def create_agent_discovery_session(
    *,
    repo_path: str,
    goal: SessionGoal,
    artifact_dir: str | Path,
    task_id: str | None = None,
    coverage_analysis_id: str | None = None,
    workspace_id: str | None = None,
) -> AgentDiscoverySession:
    return AgentDiscoverySession(
        session_id=f"agent_session_{uuid.uuid4().hex[:12]}",
        task_id=task_id,
        coverage_analysis_id=coverage_analysis_id,
        workspace_id=workspace_id,
        repo_path=str(Path(repo_path).resolve()),
        goal=goal,
        artifact_dir=Path(artifact_dir),
    )


create_agent_discovery_session.load = AgentDiscoverySession.load  # type: ignore[attr-defined]
