"""Domain-neutral, read-only Validator contracts introduced in Phase 5."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.services.validators import (
    DEFAULT_VALIDATOR_REGISTRY,
    ValidationResult,
    validate_required_output_subset,
)


def _output(
    output_id: str,
    artifact: str,
    *,
    required: bool = True,
    schema: dict | None = None,
) -> dict:
    return {
        "output_id": output_id,
        "artifact": artifact,
        "required": required,
        "schema": schema,
    }


def _tree(root: Path) -> dict[str, tuple[str, bytes | str]]:
    snapshot: dict[str, tuple[str, bytes | str]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = ("symlink", path.readlink().as_posix())
        elif path.is_file():
            snapshot[relative] = ("file", path.read_bytes())
        elif path.is_dir():
            snapshot[relative] = ("directory", "")
    return snapshot


def test_registry_exposes_domain_neutral_read_only_validators() -> None:
    assert DEFAULT_VALIDATOR_REGISTRY.ids() == (
        "artifact_exists",
        "json_schema",
        "source_evidence",
    )
    assert all(
        DEFAULT_VALIDATOR_REGISTRY.get(validator_id).read_only
        for validator_id in DEFAULT_VALIDATOR_REGISTRY.ids()
    )


def test_required_outputs_must_be_a_subset_of_declared_outputs() -> None:
    result = validate_required_output_subset(
        validator_id="source_evidence",
        node_id="validate-source",
        required_output_ids=["report", "ghost"],
        declared_outputs=[_output("report", "report.md")],
    )

    assert result == ValidationResult.failed(
        validator_id="source_evidence",
        code="undeclared_required_output",
        message="Validator 要求的输出未在工作流中声明。",
        node_id="validate-source",
        output_id="ghost",
        details={"undeclared_output_ids": ["ghost"]},
    )
    assert result.failure_kind == "validation_failed"
    assert result.provider_failed is False


def test_required_output_subset_rejects_duplicate_and_unstructured_declarations() -> None:
    duplicate = validate_required_output_subset(
        validator_id="artifact_exists",
        required_output_ids=["report"],
        declared_outputs=[
            _output("report", "report.md"),
            _output("report", "other.md"),
        ],
    )
    malformed = validate_required_output_subset(
        validator_id="artifact_exists",
        required_output_ids=["report"],
        declared_outputs=["report"],
    )

    assert duplicate.status == "failed"
    assert duplicate.issues[0].code == "duplicate_declared_output"
    assert malformed.status == "failed"
    assert malformed.issues[0].code == "invalid_declared_output"


def test_artifact_exists_checks_only_the_connected_declared_subset(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "report.md").write_text("verified report", encoding="utf-8")
    before = _tree(tmp_path)

    result = DEFAULT_VALIDATOR_REGISTRY.run(
        "artifact_exists",
        artifact_root=artifact_root,
        declared_outputs=[
            _output("report", "report.md"),
            _output("optional", "optional.md", required=False),
        ],
        required_output_ids=["report"],
    )

    assert result.status == "passed"
    assert result.validated_output_ids == ("report",)
    assert _tree(tmp_path) == before


@pytest.mark.parametrize(
    ("contents", "expected_code"),
    [(None, "artifact_missing"), (b"", "artifact_empty")],
)
def test_artifact_exists_rejects_missing_or_empty_files(
    tmp_path: Path,
    contents: bytes | None,
    expected_code: str,
) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    if contents is not None:
        (artifact_root / "report.md").write_bytes(contents)

    result = DEFAULT_VALIDATOR_REGISTRY.run(
        "artifact_exists",
        artifact_root=artifact_root,
        declared_outputs=[_output("report", "report.md")],
        required_output_ids=["report"],
    )

    assert result.status == "failed"
    assert result.issues[0].code == expected_code
    assert result.failure_kind == "validation_failed"
    assert result.provider_failed is False


def test_artifact_exists_rejects_directory_symlink_and_path_escape(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "folder").mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (artifact_root / "link.md").symlink_to(outside)

    results = {
        output_id: DEFAULT_VALIDATOR_REGISTRY.run(
            "artifact_exists",
            artifact_root=artifact_root,
            declared_outputs=[_output(output_id, artifact)],
            required_output_ids=[output_id],
        )
        for output_id, artifact in {
            "directory": "folder",
            "symlink": "link.md",
            "escape": "../outside.md",
            "absolute": str(outside),
            "windows_drive": "C:/outside.md",
            "windows_unc": r"\\server\share\outside.md",
        }.items()
    }

    assert results["directory"].issues[0].code == "artifact_not_regular_file"
    assert results["symlink"].issues[0].code == "artifact_symlink_rejected"
    assert results["escape"].issues[0].code == "artifact_path_escape"
    assert results["absolute"].issues[0].code == "artifact_path_escape"
    assert results["windows_drive"].issues[0].code == "artifact_path_escape"
    assert results["windows_unc"].issues[0].code == "artifact_path_escape"


def test_artifact_exists_rejects_symlinked_parent_directory(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "report.md").write_text("outside", encoding="utf-8")
    (artifact_root / "nested").symlink_to(outside, target_is_directory=True)

    result = DEFAULT_VALIDATOR_REGISTRY.run(
        "artifact_exists",
        artifact_root=artifact_root,
        declared_outputs=[_output("report", "nested/report.md")],
        required_output_ids=["report"],
    )

    assert result.issues[0].code == "artifact_symlink_rejected"


def test_artifact_exists_rejects_a_symlinked_artifact_root(tmp_path: Path) -> None:
    real_root = tmp_path / "real-artifacts"
    real_root.mkdir()
    (real_root / "report.md").write_text("outside boundary", encoding="utf-8")
    artifact_root = tmp_path / "artifacts"
    artifact_root.symlink_to(real_root, target_is_directory=True)

    result = DEFAULT_VALIDATOR_REGISTRY.run(
        "artifact_exists",
        artifact_root=artifact_root,
        declared_outputs=[_output("report", "report.md")],
        required_output_ids=["report"],
        node_id="validate-report",
    )

    assert result.status == "failed"
    assert result.issues[0].code == "artifact_root_symlink_rejected"
    assert result.issues[0].node_id == "validate-report"


def test_artifact_exists_rejects_a_non_directory_artifact_root(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.write_text("not a root", encoding="utf-8")

    result = DEFAULT_VALIDATOR_REGISTRY.run(
        "artifact_exists",
        artifact_root=artifact_root,
        declared_outputs=[_output("report", "report.md")],
        required_output_ids=["report"],
    )

    assert result.status == "failed"
    assert result.issues[0].code == "artifact_root_not_directory"


def test_json_schema_validates_only_connected_declared_json(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    schema = {
        "type": "object",
        "required": ["title", "items"],
        "properties": {
            "title": {"type": "string", "minLength": 1},
            "items": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {"id": {"type": "integer", "minimum": 1}},
                    "additionalProperties": False,
                },
            },
        },
        "additionalProperties": False,
    }
    (artifact_root / "result.json").write_text(
        json.dumps({"title": "Report", "items": [{"id": 1}]}),
        encoding="utf-8",
    )

    result = DEFAULT_VALIDATOR_REGISTRY.run(
        "json_schema",
        artifact_root=artifact_root,
        declared_outputs=[
            _output("result", "result.json", schema=schema),
            _output("unconnected", "missing.json", schema={"type": "array"}),
        ],
        required_output_ids=["result"],
    )

    assert result.status == "passed"
    assert result.validated_output_ids == ("result",)


@pytest.mark.parametrize(
    ("payload", "schema", "expected_code"),
    [
        ("not-json", {"type": "object"}, "artifact_invalid_json"),
        (json.dumps({"count": "one"}), {"type": "object", "required": ["count"], "properties": {"count": {"type": "integer"}}}, "json_schema_mismatch"),
        (json.dumps({"count": 1}), None, "json_schema_missing"),
        (json.dumps({"count": 1}), "not-structured", "json_schema_invalid"),
    ],
)
def test_json_schema_reports_structured_validation_failures(
    tmp_path: Path,
    payload: str,
    schema: object,
    expected_code: str,
) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "result.json").write_text(payload, encoding="utf-8")

    result = DEFAULT_VALIDATOR_REGISTRY.run(
        "json_schema",
        artifact_root=artifact_root,
        declared_outputs=[_output("result", "result.json", schema=schema)],
        required_output_ids=["result"],
    )

    assert result.status == "failed"
    assert result.issues[0].code == expected_code
    assert result.failure_kind == "validation_failed"


def test_json_schema_rejects_malformed_keywords_without_raising(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "result.json").write_text(
        json.dumps({"title": "Report"}), encoding="utf-8"
    )

    result = DEFAULT_VALIDATOR_REGISTRY.run(
        "json_schema",
        artifact_root=artifact_root,
        declared_outputs=[
            _output(
                "result",
                "result.json",
                schema={"type": "object", "minLength": "not-an-integer"},
            )
        ],
        required_output_ids=["result"],
    )

    assert result.status == "failed"
    assert result.issues[0].code == "json_schema_invalid"


def _write_source_evidence_fixture(tmp_path: Path) -> tuple[Path, Path, dict]:
    source_root = tmp_path / "repo"
    artifact_root = tmp_path / "artifacts"
    source_root.mkdir()
    artifact_root.mkdir()
    source = source_root / "src" / "queue.c"
    source.parent.mkdir()
    source_text = (
        "static int queue_submit(int value) {\n"
        "    if (value < 0) {\n"
        "        return -1;\n"
        "    }\n"
        "    return value;\n"
        "}\n"
    )
    source.write_text(source_text, encoding="utf-8")
    card = {
        "evidence_id": "SRC-001",
        "file_path": "src/queue.c",
        "start_line": 1,
        "end_line": 6,
        "excerpt": source_text.rstrip("\n"),
        "symbols": ["queue_submit"],
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    (artifact_root / "evidence.json").write_text(json.dumps([card]), encoding="utf-8")
    return artifact_root, source_root, card


def test_source_evidence_verifies_connected_declared_artifact_against_source(
    tmp_path: Path,
) -> None:
    artifact_root, source_root, _card = _write_source_evidence_fixture(tmp_path)
    before = _tree(tmp_path)

    result = DEFAULT_VALIDATOR_REGISTRY.run(
        "source_evidence",
        artifact_root=artifact_root,
        source_root=source_root,
        declared_outputs=[
            _output("evidence", "evidence.json"),
            _output("unconnected", "missing.json"),
        ],
        required_output_ids=["evidence"],
    )

    assert result.status == "passed"
    assert result.validated_output_ids == ("evidence",)
    assert result.details["verified_evidence_count"] == 1
    assert _tree(tmp_path) == before


def test_source_evidence_accepts_missing_final_newline_for_blank_terminal_line(
    tmp_path: Path,
) -> None:
    artifact_root, source_root, card = _write_source_evidence_fixture(tmp_path)
    source = source_root / card["file_path"]
    source.write_text(
        "static int queue_submit(int value) {\n"
        "    return value;\n"
        "}\n"
        "\n",
        encoding="utf-8",
    )
    card.update({
        "end_line": 4,
        "excerpt": "static int queue_submit(int value) {\n"
        "    return value;\n"
        "}",
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    })
    (artifact_root / "evidence.json").write_text(json.dumps([card]), encoding="utf-8")

    result = DEFAULT_VALIDATOR_REGISTRY.run(
        "source_evidence",
        artifact_root=artifact_root,
        source_root=source_root,
        declared_outputs=[_output("evidence", "evidence.json")],
        required_output_ids=["evidence"],
    )

    assert result.status == "passed"


def test_source_evidence_accepts_horizontal_alignment_only_difference(
    tmp_path: Path,
) -> None:
    artifact_root, source_root, card = _write_source_evidence_fixture(tmp_path)
    source = source_root / card["file_path"]
    source.write_text(
        "int queue_submit(int value) {\n"
        "\treturn value +\n"
        "\t\t\t1;\n"
        "}\n",
        encoding="utf-8",
    )
    card.update({
        "start_line": 1,
        "end_line": 4,
        "excerpt": "int queue_submit(int value) {\n"
        " return value +\n"
        " 1;\n"
        "}",
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    })
    (artifact_root / "evidence.json").write_text(json.dumps([card]), encoding="utf-8")

    result = DEFAULT_VALIDATOR_REGISTRY.run(
        "source_evidence",
        artifact_root=artifact_root,
        source_root=source_root,
        declared_outputs=[_output("evidence", "evidence.json")],
        required_output_ids=["evidence"],
    )

    assert result.status == "passed"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ({"file_path": "../outside.c"}, "source_path_escape"),
        ({"file_path": "src/missing.c"}, "source_file_missing"),
        ({"start_line": 0}, "source_line_range_invalid"),
        ({"end_line": 99}, "source_line_range_invalid"),
        ({"excerpt": "invented source"}, "source_excerpt_mismatch"),
        ({"symbols": ["invented_symbol"]}, "source_symbol_missing"),
        ({"sha256": "0" * 64}, "source_sha256_mismatch"),
    ],
)
def test_source_evidence_rejects_untrue_path_line_excerpt_symbol_or_digest(
    tmp_path: Path,
    mutation: dict,
    expected_code: str,
) -> None:
    artifact_root, source_root, card = _write_source_evidence_fixture(tmp_path)
    card.update(mutation)
    (artifact_root / "evidence.json").write_text(json.dumps([card]), encoding="utf-8")

    result = DEFAULT_VALIDATOR_REGISTRY.run(
        "source_evidence",
        artifact_root=artifact_root,
        source_root=source_root,
        declared_outputs=[_output("evidence", "evidence.json")],
        required_output_ids=["evidence"],
    )

    assert result.status == "failed"
    assert result.issues[0].code == expected_code


def test_source_evidence_rejects_symlinked_source_file(tmp_path: Path) -> None:
    artifact_root, source_root, card = _write_source_evidence_fixture(tmp_path)
    source = source_root / card["file_path"]
    outside = tmp_path / "outside.c"
    outside.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(outside)

    result = DEFAULT_VALIDATOR_REGISTRY.run(
        "source_evidence",
        artifact_root=artifact_root,
        source_root=source_root,
        declared_outputs=[_output("evidence", "evidence.json")],
        required_output_ids=["evidence"],
    )

    assert result.issues[0].code == "source_symlink_rejected"


def test_source_evidence_requires_structured_card_array(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    source_root = tmp_path / "repo"
    artifact_root.mkdir()
    source_root.mkdir()
    (artifact_root / "evidence.json").write_text(
        json.dumps({"source_evidence": "src/file.c:1"}), encoding="utf-8"
    )

    result = DEFAULT_VALIDATOR_REGISTRY.run(
        "source_evidence",
        artifact_root=artifact_root,
        source_root=source_root,
        declared_outputs=[_output("evidence", "evidence.json")],
        required_output_ids=["evidence"],
    )

    assert result.issues[0].code == "source_evidence_invalid_structure"


def test_validators_do_not_create_or_modify_user_deliverables(tmp_path: Path) -> None:
    artifact_root, source_root, _card = _write_source_evidence_fixture(tmp_path)
    declarations = [_output("evidence", "evidence.json")]
    before = _tree(tmp_path)

    for validator_id in ("artifact_exists", "source_evidence"):
        kwargs = {"source_root": source_root} if validator_id == "source_evidence" else {}
        DEFAULT_VALIDATOR_REGISTRY.run(
            validator_id,
            artifact_root=artifact_root,
            declared_outputs=declarations,
            required_output_ids=["evidence"],
            **kwargs,
        )

    assert _tree(tmp_path) == before


def test_validator_package_has_no_professional_governance_imports() -> None:
    package_root = Path(__file__).parents[1] / "app" / "services" / "validators"
    forbidden = ("governance_plugins", "test_activity_contract", "sfmea", "iscsi", "black_box")

    contents = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in package_root.glob("*.py")
    )

    assert not any(token in contents for token in forbidden)
