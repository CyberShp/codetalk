"""Evidence-safe replay of historical test knowledge.

Replay fixtures are deliberately treated as historical material.  The runner
uses them to exercise retrieval and authority transitions, but it never turns
history into a current defect conclusion without current evidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.services.knowledge_policy import authority_transition
from app.services.knowledge_retrieval import FederatedKnowledgeRetriever
from app.services.knowledge_store import KnowledgeStore


_SUPPORTED_STATUSES = {
    "investigation_lead",
    "candidate_finding",
    "confirmed_finding",
    "ruled_out",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ReplaySource:
    kind: str
    identity: str
    revision: str
    locators: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ReplayCase:
    case_id: str
    title: str
    query: str
    source: ReplaySource
    summary: str
    content: str
    terms: tuple[str, ...]
    applicability: tuple[str, ...]
    exclusions: tuple[str, ...]


def load_replay_fixtures(fixture_dir: str | Path) -> list[ReplayCase]:
    """Load and validate replay inputs in stable filename order."""
    directory = Path(fixture_dir)
    cases: list[ReplayCase] = []
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        source_payload = payload.get("source") or {}
        locator = dict(source_payload.get("locator") or {})
        locator.setdefault("path", path.name)
        required = {
            "case_id": payload.get("case_id"),
            "title": payload.get("title"),
            "query": payload.get("query"),
            "source.kind": source_payload.get("kind"),
            "source.identity": source_payload.get("identity"),
            "summary": payload.get("summary"),
            "content": payload.get("content"),
        }
        missing = [name for name, value in required.items() if not str(value or "").strip()]
        if missing:
            raise ValueError(f"replay fixture {path.name} is missing: {', '.join(missing)}")
        if not locator.get("kind"):
            raise ValueError(f"replay fixture {path.name} requires a source locator kind")
        cases.append(
            ReplayCase(
                case_id=str(payload["case_id"]),
                title=str(payload["title"]),
                query=str(payload["query"]),
                source=ReplaySource(
                    kind=str(source_payload["kind"]),
                    identity=str(source_payload["identity"]),
                    revision=str(source_payload.get("revision") or ""),
                    locators=(locator,),
                ),
                summary=str(payload["summary"]),
                content=str(payload["content"]),
                terms=tuple(_strings(payload.get("terms"))),
                applicability=tuple(_strings(payload.get("applicability"))),
                exclusions=tuple(_strings(payload.get("exclusions"))),
            )
        )
    if not cases:
        raise ValueError(f"no replay fixtures found in {directory}")
    return cases


class HistoricalReplayRunner:
    """Seed historical fixtures, retrieve them, and replay evidence states."""

    def __init__(self, store: KnowledgeStore, *, retriever: FederatedKnowledgeRetriever | None = None) -> None:
        self.store = store
        self.retriever = retriever or FederatedKnowledgeRetriever(store)

    def replay_case(
        self,
        case: ReplayCase,
        *,
        requested_status: str = "candidate_finding",
        current_evidence: Iterable[str] = (),
        disconfirming_checks: Iterable[dict[str, Any]] = (),
        current_disproof_evidence: Iterable[str] = (),
    ) -> dict[str, Any]:
        prepared = self._prepare_case(case)
        decision = self._transition(
            requested_status,
            historical_hits=prepared["retrieval"]["matched_record_ids"],
            current_evidence=current_evidence,
            disconfirming_checks=disconfirming_checks,
            current_disproof_evidence=current_disproof_evidence,
        )
        return {
            "case_id": case.case_id,
            "source": prepared["source"],
            "historical_record": prepared["historical_record"],
            "retrieval": prepared["retrieval"],
            "decision": decision,
        }

    def run(self, fixture_dir: str | Path) -> dict[str, Any]:
        scenarios: list[dict[str, Any]] = []
        for case in load_replay_fixtures(fixture_dir):
            prepared = self._prepare_case(case)
            historical_ids = prepared["retrieval"]["matched_record_ids"]
            trials = [
                self._trial(
                    case,
                    prepared,
                    "history_only",
                    requested_status="candidate_finding",
                    historical_hits=historical_ids,
                ),
                self._trial(
                    case,
                    prepared,
                    "current_support",
                    requested_status="candidate_finding",
                    historical_hits=historical_ids,
                    current_evidence=[f"current evidence for {case.case_id}"],
                ),
                self._trial(
                    case,
                    prepared,
                    "confirmed_without_checks",
                    requested_status="confirmed_finding",
                    historical_hits=historical_ids,
                    current_evidence=[f"current evidence for {case.case_id}"],
                ),
                self._trial(
                    case,
                    prepared,
                    "confirmed_with_checks",
                    requested_status="confirmed_finding",
                    historical_hits=historical_ids,
                    current_evidence=[f"current evidence for {case.case_id}"],
                    disconfirming_checks=[
                        {
                            "check": "independent lifecycle check",
                            "status": "completed",
                            "result": "counter-check passed",
                        }
                    ],
                ),
                self._trial(
                    case,
                    prepared,
                    "fallback_current_disproof",
                    requested_status="ruled_out",
                    historical_hits=historical_ids,
                    current_disproof_evidence=[f"current disproof for {case.case_id}"],
                ),
            ]
            scenarios.append(
                {
                    "case_id": case.case_id,
                    "title": case.title,
                    "classification": "historical_only",
                    "source": prepared["source"],
                    "historical_record": prepared["historical_record"],
                    "retrieval": prepared["retrieval"],
                    "trials": trials,
                }
            )

        useful = sum(1 for scenario in scenarios if scenario["retrieval"]["useful"])
        trials = [trial for scenario in scenarios for trial in scenario["trials"]]
        policy_consistent = sum(1 for trial in trials if self._policy_consistent(trial))
        return {
            "schema_version": 1,
            "report_kind": "historical_knowledge_replay",
            "generated_at": _now(),
            "conclusion_scope": "authority_transition_consistency",
            "scenarios": scenarios,
            "metrics": {
                "retrieval_usefulness": useful / len(scenarios) if scenarios else 0.0,
                "conclusion_precision": policy_consistent / len(trials) if trials else 0.0,
                "retrieval_useful_scenarios": useful,
                "scenario_count": len(scenarios),
                "policy_consistent_trials": policy_consistent,
                "trial_count": len(trials),
            },
        }

    def write_report(self, fixture_dir: str | Path, output_path: str | Path) -> dict[str, Any]:
        report = self.run(fixture_dir)
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return report

    def _prepare_case(self, case: ReplayCase) -> dict[str, Any]:
        source = self.store.register_source(
            source_kind=case.source.kind,
            source_identity=case.source.identity,
            content=case.content.encode("utf-8"),
            scope="personal_global",
            revision=case.source.revision,
            locators=case.source.locators,
        )
        incident = self.store.create_incident(
            title=case.title,
            summary=case.summary,
            scope="personal_global",
            source_snapshot_ids=[source["source_snapshot_id"]],
            terms=case.terms,
        )
        pattern = self.store.create_pattern(
            name=case.title,
            content=case.content,
            scope="personal_global",
            terms=case.terms,
            applicability=case.applicability,
            exclusions=case.exclusions,
        )
        self.store.link_incident_pattern(incident["incident_id"], pattern["pattern_id"], pattern["active_version_id"])
        retrieval_result = self.retriever.retrieve(case.query, max_results=12)
        records = retrieval_result.records
        matched_ids = [str(record.get("record_id")) for record in records if record.get("record_id")]
        return {
            "source": {
                **source,
                "kind": case.source.kind,
                "identity": case.source.identity,
                "revision": case.source.revision,
                "locators": self.store.list_source_locators(source["source_snapshot_id"]),
            },
            "historical_record": {
                "incident_id": incident["incident_id"],
                "pattern_id": pattern["pattern_id"],
                "content": case.content,
                "review_state": pattern["review_state"],
                "authority": "historical_only",
            },
            "retrieval": {
                "query": case.query,
                "matched_record_ids": matched_ids,
                "useful": pattern["pattern_id"] in matched_ids,
                "fts_candidate_count": retrieval_result.fts_candidate_count,
                "embedding_status": retrieval_result.embedding_status,
                "provider_statuses": retrieval_result.provider_statuses,
            },
        }

    def _trial(
        self,
        case: ReplayCase,
        prepared: dict[str, Any],
        trial_id: str,
        *,
        requested_status: str,
        historical_hits: list[str],
        current_evidence: Iterable[str] = (),
        disconfirming_checks: Iterable[dict[str, Any]] = (),
        current_disproof_evidence: Iterable[str] = (),
    ) -> dict[str, Any]:
        current = _strings(current_evidence)
        checks = [dict(check) for check in disconfirming_checks]
        disproof = _strings(current_disproof_evidence)
        decision = self._transition(
            requested_status,
            historical_hits=historical_hits,
            current_evidence=current,
            disconfirming_checks=checks,
            current_disproof_evidence=disproof,
        )
        return {
            "trial_id": trial_id,
            "evidence_mode": "synthetic_policy_probe",
            "requested_status": requested_status,
            "historical_hits": historical_hits,
            "current_evidence": current,
            "disconfirming_checks": checks,
            "current_disproof_evidence": disproof,
            "decision": decision,
        }

    @staticmethod
    def _transition(
        requested_status: str,
        *,
        historical_hits: list[str],
        current_evidence: Iterable[str],
        disconfirming_checks: Iterable[dict[str, Any]],
        current_disproof_evidence: Iterable[str],
    ) -> dict[str, Any]:
        requested = str(requested_status)
        if requested not in _SUPPORTED_STATUSES:
            raise ValueError(f"unsupported replay authority status: {requested}")
        decision = authority_transition(
            requested,
            historical_hits=historical_hits,
            current_evidence=tuple(_strings(current_evidence)),
            disconfirming_checks=tuple(dict(check) for check in disconfirming_checks),
            current_disproof_evidence=tuple(_strings(current_disproof_evidence)),
        )
        return {
            "status": decision.status,
            "missing_evidence": list(decision.missing_evidence),
        }

    @staticmethod
    def _policy_consistent(trial: dict[str, Any]) -> bool:
        decision = trial["decision"]
        status = decision["status"]
        if status == "confirmed_finding":
            return bool(trial["current_evidence"]) and all(
                isinstance(check, dict)
                and str(check.get("status") or "") == "completed"
                and bool(str(check.get("result") or "").strip())
                for check in trial["disconfirming_checks"]
            )
        if status == "ruled_out":
            return bool(trial["current_disproof_evidence"])
        if not trial["current_evidence"] and not trial["current_disproof_evidence"]:
            return status == "investigation_lead"
        return status in {"investigation_lead", "candidate_finding"}


def _strings(values: Iterable[Any] | None) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values or () if str(value).strip()))
