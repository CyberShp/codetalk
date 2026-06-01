from pathlib import Path

import pytest

from app.services.analysis_artifacts import (
    build_analysis_artifact_bundle,
    format_artifacts_for_report_qa,
    write_analysis_artifacts,
)


class FakeCard:
    def __init__(
        self,
        *,
        card_id: str = "card-1",
        object_id: str = "obj-1",
        title: str = "TLS receive path",
        source: str = "repo_search",
        confidence: str = "high",
        file_path: str = "lib/tls.c",
        symbol: str = "tls_recv",
        snippet: str = "",
        notes: list[str] | None = None,
        needs_verification: bool = False,
    ) -> None:
        self.card_id = card_id
        self.object_id = object_id
        self.title = title
        self.source = source
        self.confidence = confidence
        self.file_path = file_path
        self.symbol = symbol
        self.snippet = snippet
        self.notes = notes or []
        self.needs_verification = needs_verification

    def to_dict(self) -> dict:
        return {
            "card_id": self.card_id,
            "object_id": self.object_id,
            "title": self.title,
            "source": self.source,
            "confidence": self.confidence,
            "file_path": self.file_path,
            "symbol": self.symbol,
            "snippet": self.snippet,
            "notes": self.notes,
            "needs_verification": self.needs_verification,
        }


def test_artifact_bundle_maps_claims_to_evidence_and_gaps() -> None:
    card = FakeCard(
        snippet="""
int tls_recv(struct conn *c) {
    int rc = SSL_read(c->ssl, c->buf, sizeof(c->buf));
    if (rc <= 0) {
        int err = SSL_get_error(c->ssl, rc);
        c->state = CONN_CLOSED;
        free(c->buf);
        return -EIO;
    }
    return rc;
}
""".strip()
    )
    mapping = {
        "task_id": "task-1",
        "objects": [
            {
                "object_id": "obj-1",
                "text": "TLS read error handling",
                "coverage_status": "direct_evidence",
                "unit_id": "unit_1",
                "unit_title": "TLS receive",
                "evidence_card_ids": ["card-1"],
            },
            {
                "object_id": "obj-2",
                "text": "TLS timeout retry",
                "coverage_status": "unresolved",
                "unit_id": None,
                "unit_title": None,
                "evidence_card_ids": [],
                "warnings": ["not resolved"],
            },
        ],
        "units": [
            {
                "unit_id": "unit_1",
                "title": "TLS receive",
                "object_ids": ["obj-1"],
                "evidence_card_ids": ["card-1"],
                "files": ["lib/tls.c"],
            }
        ],
    }

    bundle = build_analysis_artifact_bundle(
        task_id="task-1",
        analysis_unit_mapping=mapping,
        evidence_cards=[card],
        analysis_units=[],
    )

    claim_map = bundle["claim_evidence_map"]
    assert claim_map["version"] == "claim-evidence-map-v1"
    assert claim_map["claims"][0]["claim_id"] == "claim:obj-1"
    assert claim_map["claims"][0]["evidence_card_ids"] == ["card-1"]
    assert claim_map["claims"][1]["status"] == "gap"
    assert "not resolved" in claim_map["claims"][1]["uncertainty"]

    matrix = bundle["function_failure_matrix"]
    row = matrix["functions"][0]
    assert row["function"] == "tls_recv"
    assert "SSL_get_error" in " ".join(row["error_signals"])
    assert "free(c->buf);" in " ".join(row["cleanup_signals"])
    assert "c->state = CONN_CLOSED;" in " ".join(row["state_transitions"])
    assert row["evidence_card_ids"] == ["card-1"]

    branches = bundle["branch_deep_dive"]["branches"]
    assert branches[0]["function"] == "tls_recv"
    assert "rc <= 0" in branches[0]["condition"]
    assert branches[0]["evidence_card_id"] == "card-1"


@pytest.mark.asyncio
async def test_write_analysis_artifacts_creates_stable_json_files(tmp_path: Path) -> None:
    card = FakeCard(snippet="int fail(void) { if (errno) { return -EINVAL; } return 0; }")
    mapping = {
        "task_id": "task-json",
        "objects": [
            {
                "object_id": "obj-1",
                "text": "failure branch",
                "coverage_status": "direct_evidence",
                "unit_id": "unit_1",
                "unit_title": "failure",
                "evidence_card_ids": ["card-1"],
            }
        ],
        "units": [],
    }

    written = await write_analysis_artifacts(
        output_dir=tmp_path,
        task_id="task-json",
        analysis_unit_mapping=mapping,
        evidence_cards=[card],
        analysis_units=[],
    )

    assert {p.name for p in written} == {
        "claim_evidence_map.json",
        "function_failure_matrix.json",
        "branch_deep_dive.json",
    }
    assert (tmp_path / "claim_evidence_map.json").read_text(encoding="utf-8").startswith("{\n")


@pytest.mark.asyncio
async def test_pipeline_writer_places_artifacts_next_to_task_outputs(tmp_path: Path, monkeypatch) -> None:
    from app.config import settings
    from app.services.analysis_pipeline import AnalysisPipeline

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    pipeline = AnalysisPipeline()
    pipeline._task_id = "task-pipeline"
    pipeline._evidence_cards = [
        FakeCard(snippet="int fail(void) { if (errno) { return -EINVAL; } return 0; }")
    ]
    pipeline._analysis_units = []
    mapping = {
        "task_id": "task-pipeline",
        "objects": [
            {
                "object_id": "obj-1",
                "text": "failure branch",
                "coverage_status": "direct_evidence",
                "unit_id": "unit_1",
                "unit_title": "failure",
                "evidence_card_ids": ["card-1"],
            }
        ],
        "units": [],
    }

    await pipeline._write_analysis_artifacts(mapping)

    out_dir = settings.outputs_path / "task-pipeline"
    assert (out_dir / "claim_evidence_map.json").exists()
    assert (out_dir / "function_failure_matrix.json").exists()
    assert (out_dir / "branch_deep_dive.json").exists()


def test_format_artifacts_for_report_qa_mentions_evidence_functions_and_gaps() -> None:
    bundle = {
        "claim_evidence_map": {
            "claims": [
                {
                    "claim_id": "claim:obj-1",
                    "claim": "TLS read error handling",
                    "status": "supported",
                    "evidence_card_ids": ["card-1"],
                    "files": ["lib/tls.c"],
                    "symbols": ["tls_recv"],
                    "uncertainty": [],
                },
                {
                    "claim_id": "claim:obj-2",
                    "claim": "TLS timeout retry",
                    "status": "gap",
                    "evidence_card_ids": [],
                    "files": [],
                    "symbols": [],
                    "uncertainty": ["not resolved"],
                },
            ]
        },
        "function_failure_matrix": {
            "functions": [
                {
                    "function": "tls_recv",
                    "file_path": "lib/tls.c",
                    "branch_conditions": ["if (rc <= 0) {"],
                    "error_signals": ["SSL_get_error(c->ssl, rc);"],
                    "cleanup_signals": ["free(c->buf);"],
                    "state_transitions": ["c->state = CONN_CLOSED;"],
                    "evidence_card_ids": ["card-1"],
                    "gaps": [],
                }
            ]
        },
        "branch_deep_dive": {
            "branches": [
                {
                    "function": "tls_recv",
                    "file_path": "lib/tls.c",
                    "condition": "if (rc <= 0) {",
                    "evidence_card_id": "card-1",
                    "test_trigger_hint": "Force input/state to satisfy: if (rc <= 0) {",
                }
            ]
        },
    }

    text = format_artifacts_for_report_qa(bundle, query="SSL_get_error")

    assert "CODETALK_ANALYSIS_ARTIFACTS" in text
    assert "claim:obj-1" in text
    assert "tls_recv" in text
    assert "SSL_get_error" in text
    assert "gap" in text
