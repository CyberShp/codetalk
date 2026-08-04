"""Execute F012 candidate generation through CodeTalk's real Workbench path."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.config import settings
from app.services.agent_sandbox import benchmark_agent_sandbox
from app.services.workbench_artifact_manifest import write_task_artifact_manifest
from app.services.workbench_task_run import WorkbenchTaskRunPreparer
from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner, _preview_bytes
from app.services.workflow_dsl import WorkflowStore

_BENCHMARK_CODEX_PROVIDER = "agent-runtime:default-codex"
_BENCHMARK_RUNTIME_SCHEMA = """
CREATE TABLE agent_runtimes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    provider TEXT NOT NULL,
    command TEXT NOT NULL,
    args_json TEXT DEFAULT '[]',
    prompt_transport TEXT NOT NULL,
    output_mode TEXT NOT NULL,
    working_dir_mode TEXT NOT NULL,
    fixed_working_dir TEXT DEFAULT '',
    env_json TEXT DEFAULT '{}',
    health_command TEXT DEFAULT '',
    timeout_seconds INTEGER DEFAULT 900,
    completion_mode TEXT NOT NULL DEFAULT 'process_exit',
    idle_complete_seconds INTEGER DEFAULT 5,
    sentinel_text TEXT DEFAULT '',
    session_persistence TEXT NOT NULL DEFAULT 'none',
    resume_args_json TEXT DEFAULT '[]',
    mcp_profile TEXT DEFAULT '',
    requires_network INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class BenchmarkWorkbenchResult:
    task_run_id: str
    status: str
    task_artifact_dir: Path
    response_path: Path
    first_response_path: Path
    repair_attempt_count: int
    terminal_block_reason: str | None
    first_provenance: dict[str, Any] = field(default_factory=dict)
    final_provenance: dict[str, Any] = field(default_factory=dict)
    work_sufficiency: dict[str, Any] = field(default_factory=dict)
    repair_audit: dict[str, Any] = field(default_factory=dict)
    credential_fingerprints: tuple[tuple[int, str], ...] = field(
        default=(), repr=False, compare=False
    )


class BenchmarkWorkbenchError(RuntimeError):
    """A benchmark TaskRun failed before a candidate could be exported."""


class BenchmarkWorkbenchQualityBlocked(BenchmarkWorkbenchError):
    """A bounded evaluator-owned repair was exhausted without a safe result."""


def execute_quality_benchmark_workbench(
    *,
    case_id: str,
    source_dir: Path,
    workbench_root: Path,
    model: str,
    mode: str,
    deadline_monotonic: float,
    prompt: str,
    output_schema: Mapping[str, Any],
    approved_network_targets: tuple[str, ...] = (),
    prepublication_gate: Callable[[Path], dict[str, Any]] | None = None,
) -> BenchmarkWorkbenchResult:
    """Prepare and execute one benchmark as an ordinary Workbench TaskRun."""

    _require_remaining(deadline_monotonic)
    workbench_root.mkdir(parents=True, exist_ok=False)
    workflow = _benchmark_workflow(
        prompt=prompt,
        output_schema=output_schema,
        timeout_seconds=max(1, int(_require_remaining(deadline_monotonic))),
    )
    store = WorkflowStore(workbench_root / "workflows.sqlite3")
    preparer = WorkbenchTaskRunPreparer(
        artifact_root=workbench_root / "task_runs",
        workflow_store=store,
    )
    first_capture = workbench_root / ".first-benchmark-response.json"
    first_provenance: dict[str, Any] = {}

    def capture_first_response(kind: str, payload: dict[str, Any]) -> None:
        if first_capture.exists() or kind != "quality_repair_started":
            return
        if str(payload.get("step_id") or "") != "analyze":
            return
        try:
            attempt = int(payload.get("attempt"))
        except (TypeError, ValueError):
            return
        if attempt != 1:
            return
        for response in (workbench_root / "task_runs").glob(
            "*/agent_runs/analyze/benchmark_response.json"
        ):
            audit_path = (
                response.parent
                / "quality_repairs"
                / "attempt_1"
                / "quality_audit_before.json"
            )
            audit = _read_mapping(audit_path)
            if str(audit.get("status") or "") not in {"needs_rework", "invalid"}:
                continue
            try:
                first_bytes = response.read_bytes()
                if first_bytes != response.read_bytes():
                    continue
                parsed = json.loads(first_bytes.decode("utf-8"))
                if not isinstance(parsed, dict):
                    continue
                first_capture.write_bytes(first_bytes)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            first_provenance.update(
                {
                    "attempt": 0,
                    "event": "quality_repair_started",
                    "response_sha256": hashlib.sha256(first_bytes).hexdigest(),
                    "quality_audit_sha256": hashlib.sha256(
                        audit_path.read_bytes()
                    ).hexdigest(),
                }
            )
            return

    with benchmark_agent_sandbox(
        source_dir=source_dir,
        model=model,
        mode=mode,
        approved_network_targets=approved_network_targets,
    ) as sandbox_security:
        with _benchmark_managed_codex_runtime(
            workbench_root=workbench_root,
            model=model,
            mode=mode,
            deadline_monotonic=deadline_monotonic,
        ) as runtime_evidence:
            prepared = preparer.prepare(
                workflow_id=str(workflow["id"]),
                workspace_id=f"quality-benchmark-{case_id}",
                repo_path=str(source_dir),
                inputs={"benchmark_request": prompt},
                execution_profile_id=mode,
                workflow_snapshot_override=workflow,
                task_context={"task_id": case_id, "goal": prompt},
            )
        task_artifact = Path(prepared.artifact_dir).resolve()
        agent_artifact = task_artifact / "agent_runs" / "analyze"
        output_schema_path = agent_artifact / "benchmark_output_schema.json"
        output_schema_sha256 = _write_benchmark_output_schema(
            output_schema_path, output_schema
        )
        runtime_evidence.update(
            {
                "output_schema_artifact": (
                    "agent_runs/analyze/benchmark_output_schema.json"
                ),
                "output_schema_sha256": output_schema_sha256,
                "output_artifact": "agent_runs/analyze/benchmark_response.json",
                "invocation_artifact": (
                    "agent_runs/analyze/benchmark_codex_invocation.json"
                ),
            }
        )
        _write_runtime_evidence(
            task_artifact / "benchmark_runtime.json", runtime_evidence
        )
        _require_remaining(deadline_monotonic)
        runner = WorkbenchWorkflowRunner(
            workbench_root / "task_runs",
            event_sink=capture_first_response,
            is_cancelled=lambda: time.monotonic() >= deadline_monotonic,
        )
        execution = runner.execute_task_run(
            prepared.task_run_id,
            timeout_sec=max(1, int(_require_remaining(deadline_monotonic))),
        )
        response_path = agent_artifact / "benchmark_response.json"
        if not response_path.is_file():
            raise BenchmarkWorkbenchError("workbench candidate artifact is unavailable")
        if prepublication_gate is not None:
            _run_evaluator_owned_prepublication_repair(
                runner=runner,
                task_run=runner.store.load(prepared.task_run_id),
                step_results=list(getattr(execution, "step_results", ()) or ()),
                response_path=response_path,
                task_artifact=task_artifact,
                gate=prepublication_gate,
                deadline_monotonic=deadline_monotonic,
            )

    _require_remaining(deadline_monotonic)
    task_artifact = Path(prepared.artifact_dir).resolve()
    agent_artifact = task_artifact / "agent_runs" / "analyze"
    _verify_runtime_file_hash(
        agent_artifact / "benchmark_output_schema.json",
        str(runtime_evidence["output_schema_sha256"]),
        failure_message="benchmark output schema changed during execution",
    )
    _verify_runtime_file_hash(
        Path(str(runtime_evidence["command"])),
        str(runtime_evidence["wrapper_sha256"]),
        failure_message="benchmark Codex wrapper changed during execution",
    )
    invocation_sha256 = _validate_benchmark_codex_invocation(
        agent_artifact=agent_artifact,
        runtime_evidence=runtime_evidence,
    )
    runtime_evidence["invocation_sha256"] = invocation_sha256
    _write_runtime_evidence(
        task_artifact / "benchmark_runtime.json", runtime_evidence
    )
    repair_summary = _load_repair_summary(task_artifact, agent_artifact)
    repair_audit = _load_repair_audit(task_artifact, agent_artifact)
    repair_attempt_count = int(repair_summary.get("attempt_count") or 0)
    if (
        repair_attempt_count == 0
        and int(repair_audit.get("last_accepted_attempt") or 0) > 0
        and first_capture.is_file()
        and hashlib.sha256(first_capture.read_bytes()).hexdigest()
        != hashlib.sha256(response_path.read_bytes()).hexdigest()
    ):
        repair_attempt_count = int(repair_audit["last_accepted_attempt"])
    final_provenance = _validated_final_provenance(
        task_artifact=task_artifact,
        response_path=response_path,
        repair_attempt_count=repair_attempt_count,
        expected_status=str(execution.status),
    )
    if repair_attempt_count == 0:
        first_path = response_path
        first_provenance = {
            **final_provenance,
            "attempt": 0,
            "event": "workflow_output_validated",
        }
    else:
        if not first_capture.is_file() or not first_provenance:
            raise BenchmarkWorkbenchError(
                "validated attempt-zero benchmark snapshot is unavailable"
            )
        if first_provenance.get("response_sha256") == final_provenance.get(
            "response_sha256"
        ):
            raise BenchmarkWorkbenchError(
                "successful repair did not produce a distinct validated response"
            )
        first_path = first_capture
    return BenchmarkWorkbenchResult(
        task_run_id=prepared.task_run_id,
        status=str(execution.status),
        task_artifact_dir=task_artifact,
        response_path=response_path,
        first_response_path=first_path,
        repair_attempt_count=repair_attempt_count,
        terminal_block_reason=(
            str(repair_summary.get("terminal_block_reason"))
            if repair_summary.get("terminal_block_reason")
            else None
        ),
        first_provenance=first_provenance,
        final_provenance=final_provenance,
        work_sufficiency=_read_mapping(
            task_artifact / "benchmark_work_sufficiency.json"
        ),
        repair_audit=repair_audit,
        credential_fingerprints=tuple(
            sorted(getattr(sandbox_security, "credential_fingerprints", ()))
        ),
    )


def _run_evaluator_owned_prepublication_repair(
    *,
    runner: Any,
    task_run: Any,
    step_results: list[dict[str, Any]],
    response_path: Path,
    task_artifact: Path,
    gate: Callable[[Path], dict[str, Any]],
    deadline_monotonic: float,
) -> dict[str, Any]:
    """Reuse the existing bounded Workbench repair for a public-safe gate."""

    _require_remaining(deadline_monotonic)
    audit = _validated_prepublication_audit(gate(response_path))
    if audit["status"] == "completed":
        return {"attempt_count": 0, "successful_attempt_count": 0}
    original_bytes = response_path.read_bytes()
    outputs_path = task_artifact / "workflow_outputs.json"
    original_outputs = outputs_path.read_bytes()
    execution_path = task_artifact / "workflow_execution.json"
    manifest_path = task_artifact / "task_artifact_manifest.json"
    try:
        original_execution = execution_path.read_bytes()
        original_manifest = manifest_path.read_bytes()
    except OSError as exc:
        raise BenchmarkWorkbenchError(
            "workbench repair control artifacts are unavailable"
        ) from exc
    summary_path = task_artifact / "quality_repair_summary.json"
    original_summary = summary_path.read_bytes() if summary_path.is_file() else None
    persisted_summary = _read_mapping(summary_path)
    existing_summary = {
        **_load_repair_summary(task_artifact, response_path.parent),
        **persisted_summary,
    }
    existing_attempts = max(0, int(existing_summary.get("attempt_count") or 0))
    existing_successes = max(
        0,
        int(
            existing_summary.get("successful_attempt_count")
            or existing_summary.get("attempt_count")
            or 0
        ),
    )
    attempt_number = existing_attempts + 1
    try:
        repair = runner._attempt_external_agent_quality_repair(
            task_run=task_run,
            step_results=step_results,
            audit=audit,
            deadline_monotonic=deadline_monotonic,
            attempt_number=attempt_number,
        )
        if not repair.get("attempted") or not repair.get("candidate_ready"):
            raise BenchmarkWorkbenchQualityBlocked(
                "evaluator-owned benchmark repair could not be executed"
            )
        _require_remaining(deadline_monotonic)
        final_audit = _validated_prepublication_audit(gate(response_path))
        if final_audit["status"] != "completed":
            raise BenchmarkWorkbenchQualityBlocked(
                "evaluator-owned benchmark repair did not clear the diagnostic"
            )
        final_bytes = response_path.read_bytes()
        if final_bytes == original_bytes:
            raise BenchmarkWorkbenchQualityBlocked(
                "evaluator-owned benchmark repair did not change the candidate"
            )
        _rewrite_workflow_output(
            outputs_path,
            artifact="benchmark_response.json",
            data=final_bytes,
        )
        _rewrite_workflow_output(
            execution_path,
            artifact="benchmark_response.json",
            data=final_bytes,
        )
        summary = {
            **existing_summary,
            "attempt_count": attempt_number,
            "successful_attempt_count": existing_successes + 1,
            "terminal_block_reason": None,
            "evaluator_prepublication_gate": {
                "status_before": "needs_rework",
                "status_after": "completed",
            },
        }
        _write_runtime_evidence(summary_path, summary)
        write_task_artifact_manifest(
            task_artifact,
            task_run_id=str(getattr(task_run, "task_run_id", "") or ""),
        )
        return summary
    except BaseException as exc:
        response_path.write_bytes(original_bytes)
        outputs_path.write_bytes(original_outputs)
        execution_path.write_bytes(original_execution)
        if original_summary is None:
            summary_path.unlink(missing_ok=True)
        else:
            summary_path.write_bytes(original_summary)
        manifest_path.write_bytes(original_manifest)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        if isinstance(exc, BenchmarkWorkbenchQualityBlocked):
            raise
        raise BenchmarkWorkbenchQualityBlocked(
            "evaluator-owned benchmark repair failed and was rolled back"
        ) from exc


def _validated_prepublication_audit(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BenchmarkWorkbenchError("evaluator-owned benchmark gate is invalid")
    status = str(value.get("status") or "").strip()
    raw_issues = value.get("issues") or []
    if status == "completed" and not raw_issues:
        return {"status": "completed", "issues": []}
    if status != "needs_rework" or not isinstance(raw_issues, list) or not raw_issues:
        raise BenchmarkWorkbenchError("evaluator-owned benchmark gate is invalid")
    issues: list[dict[str, Any]] = []
    for raw_issue in raw_issues:
        if not isinstance(raw_issue, Mapping):
            raise BenchmarkWorkbenchError("evaluator-owned benchmark gate is invalid")
        issue = {
            "code": str(raw_issue.get("code") or "").strip(),
            "artifact": str(raw_issue.get("artifact") or "").strip(),
            "field": str(raw_issue.get("field") or "").strip(),
            "row_id": str(raw_issue.get("row_id") or "").strip(),
            "operation": str(raw_issue.get("operation") or "").strip(),
            "repairable": raw_issue.get("repairable") is True,
        }
        if issue != {
            "code": "compound_claim_requires_split",
            "artifact": "benchmark_response.json",
            "field": "claims",
            "row_id": issue["row_id"],
            "operation": "split_candidate_statement",
            "repairable": True,
        } or not issue["row_id"]:
            raise BenchmarkWorkbenchError("evaluator-owned benchmark gate is invalid")
        issues.append(issue)
    return {"status": "needs_rework", "deliverable": False, "issues": issues}


def _rewrite_workflow_output(path: Path, *, artifact: str, data: bytes) -> None:
    payload = _read_mapping(path)
    outputs = payload.get("outputs")
    if not isinstance(outputs, list):
        raise BenchmarkWorkbenchError("validated workflow output contract is unavailable")
    matched = 0
    for output in outputs:
        if isinstance(output, dict) and str(output.get("artifact") or "") == artifact:
            output["sha256"] = hashlib.sha256(data).hexdigest()
            output["size_bytes"] = len(data)
            output["preview"] = _preview_bytes(data)
            matched += 1
    if matched != 1:
        raise BenchmarkWorkbenchError("validated benchmark output is unavailable")
    _write_runtime_evidence(path, payload)


@contextmanager
def _benchmark_managed_codex_runtime(
    *,
    workbench_root: Path,
    model: str,
    mode: str,
    deadline_monotonic: float,
):
    runtime = _resolve_benchmark_codex_runtime(
        model=model,
        mode=mode,
        deadline_monotonic=deadline_monotonic,
    )
    wrapper_path, wrapper_sha256 = _materialize_benchmark_codex_wrapper(
        workbench_root=workbench_root,
        codex_command=Path(str(runtime["command"])),
    )
    runtime["codex_command"] = runtime["command"]
    runtime["command"] = str(wrapper_path)
    runtime["wrapper_sha256"] = wrapper_sha256
    descriptor, database_name = tempfile.mkstemp(
        prefix=".benchmark-runtime-", suffix=".sqlite3", dir=workbench_root
    )
    os.close(descriptor)
    database_path = Path(database_name)
    original_database = settings.sqlite_db
    try:
        with sqlite3.connect(database_path) as database:
            database.executescript(_BENCHMARK_RUNTIME_SCHEMA)
            database.execute(
                """
                INSERT INTO agent_runtimes
                    (id, name, provider, command, args_json, prompt_transport,
                     output_mode, working_dir_mode, env_json, timeout_seconds,
                     completion_mode, session_persistence, resume_args_json,
                     requires_network, enabled, created_at, updated_at)
                VALUES
                    (?, ?, 'codex', ?, ?, 'codex_exec_json', 'stream_json',
                     'project', '{}', ?, 'process_exit', 'resume_args', '[]',
                     1, 1, ?, ?)
                """,
                (
                    "default-codex",
                    f"Benchmark Codex ({runtime['cli_version']})",
                    runtime["command"],
                    json.dumps(runtime["args"], ensure_ascii=True),
                    max(1, int(_require_remaining(deadline_monotonic))),
                    runtime["bound_at"],
                    runtime["bound_at"],
                ),
            )
            database.commit()
        _require_remaining(deadline_monotonic)
        settings.sqlite_db = str(database_path)
        yield runtime
    finally:
        settings.sqlite_db = original_database
        for suffix in ("", "-journal", "-shm", "-wal"):
            Path(f"{database_path}{suffix}").unlink(missing_ok=True)


def _resolve_benchmark_codex_runtime(
    *, model: str, mode: str, deadline_monotonic: float
) -> dict[str, Any]:
    _require_remaining(deadline_monotonic)
    command = shutil.which("codex")
    if not command:
        raise BenchmarkWorkbenchError("managed Codex executable is unavailable")
    command_path = Path(command)
    try:
        resolved_command = command_path.resolve(strict=True)
    except OSError as exc:
        raise BenchmarkWorkbenchError("managed Codex executable is unavailable") from exc
    if not resolved_command.is_file() or not os.access(command_path, os.X_OK):
        raise BenchmarkWorkbenchError("managed Codex executable is unavailable")
    try:
        version_result = subprocess.run(
            [str(command_path), "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=min(5.0, _require_remaining(deadline_monotonic)),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BenchmarkWorkbenchError("managed Codex version probe failed") from exc
    version = (version_result.stdout or version_result.stderr).strip().splitlines()
    cli_version = version[0].strip() if version else ""
    if (
        version_result.returncode != 0
        or not cli_version
        or len(cli_version) > 200
        or any(ord(char) < 32 for char in cli_version)
    ):
        raise BenchmarkWorkbenchError("managed Codex version probe failed")
    executable_sha256 = _hash_runtime_executable(
        resolved_command, deadline_monotonic=deadline_monotonic
    )
    reasoning_effort = "high" if mode == "deep" else "low"
    runtime_args = [
        "exec",
        "-m",
        model,
        "-c",
        f'model_reasoning_effort="{reasoning_effort}"',
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
    ]
    return {
        "schema_version": "quality-benchmark-runtime-v1",
        "runtime_id": "default-codex",
        "provider": _BENCHMARK_CODEX_PROVIDER,
        "command": str(command_path),
        "args": runtime_args,
        "prompt_transport": "codex_exec_json",
        "requires_network": True,
        "model": model,
        "mode": mode,
        "model_reasoning_effort": reasoning_effort,
        "cli_version": cli_version,
        "executable_sha256": executable_sha256,
        "bound_at": str(time.time_ns()),
    }


def _hash_runtime_executable(path: Path, *, deadline_monotonic: float) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                _require_remaining(deadline_monotonic)
                digest.update(chunk)
    except OSError as exc:
        raise BenchmarkWorkbenchError("managed Codex executable hash failed") from exc
    return digest.hexdigest()


def _materialize_benchmark_codex_wrapper(
    *, workbench_root: Path, codex_command: Path
) -> tuple[Path, str]:
    boundary = workbench_root.resolve(strict=True)
    wrapper_source = f'''#!/usr/bin/python3
import json
import os
import sys
from pathlib import Path

BOUNDARY = Path({str(boundary)!r})
CODEX_COMMAND = {str(codex_command)!r}


def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(78)


artifact_text = os.environ.get("CODETALK_AGENT_ARTIFACT_DIR", "")
if not artifact_text or not os.path.isabs(artifact_text):
    fail("benchmark artifact boundary is unavailable")
artifact_input = Path(os.path.abspath(artifact_text))
try:
    artifact_dir = artifact_input.resolve(strict=True)
    task_runs = (BOUNDARY / "task_runs").resolve(strict=True)
    relative = artifact_dir.relative_to(task_runs)
except (OSError, ValueError):
    fail("benchmark artifact boundary is invalid")
if len(relative.parts) != 3:
    fail("benchmark artifact boundary is invalid")
if relative.parts[1:] != ("agent_runs", "analyze"):
    fail("benchmark artifact boundary is invalid")
if artifact_dir.is_symlink() or not artifact_dir.is_dir():
    fail("benchmark artifact boundary is invalid")

schema_path = artifact_dir / "benchmark_output_schema.json"
output_path = artifact_dir / "benchmark_response.json"
if schema_path.is_symlink() or not schema_path.is_file():
    fail("benchmark output schema is unavailable")
if schema_path.resolve(strict=True).parent != artifact_dir:
    fail("benchmark output schema boundary is invalid")
if output_path.is_symlink() or (output_path.exists() and not output_path.is_file()):
    fail("benchmark output artifact boundary is invalid")

args = list(sys.argv[1:])
if any(flag in args for flag in ("--output-schema", "-o", "--output-last-message")):
    fail("benchmark output arguments are already present")
final_argv = [
    CODEX_COMMAND,
    *args,
    "--output-schema",
    str(schema_path),
    "--output-last-message",
    str(output_path),
]
invocation_path = artifact_dir / "benchmark_codex_invocation.json"
invocation_temp = artifact_dir / ".benchmark_codex_invocation.tmp"
if invocation_path.is_symlink() or invocation_temp.is_symlink():
    fail("benchmark invocation evidence boundary is invalid")
with invocation_temp.open("w", encoding="utf-8") as stream:
    json.dump(
        {{"schema_version": "quality-benchmark-codex-invocation-v1", "argv": final_argv}},
        stream,
        ensure_ascii=True,
        indent=2,
    )
    stream.write("\\n")
    stream.flush()
    os.fsync(stream.fileno())
os.replace(invocation_temp, invocation_path)
os.execv(
    CODEX_COMMAND,
    final_argv,
)
'''
    encoded = wrapper_source.encode("utf-8")
    wrapper_directory = Path(
        tempfile.mkdtemp(prefix=".benchmark-codex-wrapper-", dir=workbench_root)
    )
    wrapper_path = wrapper_directory / "codex"
    descriptor = os.open(
        wrapper_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o500
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        wrapper_path.chmod(0o500)
    except BaseException:
        wrapper_path.unlink(missing_ok=True)
        wrapper_directory.rmdir()
        raise
    return wrapper_path, hashlib.sha256(encoded).hexdigest()


def _write_benchmark_output_schema(
    path: Path, output_schema: Mapping[str, Any]
) -> str:
    encoded = (
        json.dumps(
            dict(output_schema),
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)
    return hashlib.sha256(encoded).hexdigest()


def _verify_runtime_file_hash(
    path: Path, expected_sha256: str, *, failure_message: str
) -> None:
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError
        actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise BenchmarkWorkbenchError(failure_message) from exc
    if actual_sha256 != expected_sha256:
        raise BenchmarkWorkbenchError(failure_message)


def _validate_benchmark_codex_invocation(
    *, agent_artifact: Path, runtime_evidence: Mapping[str, Any]
) -> str:
    invocation_path = agent_artifact / "benchmark_codex_invocation.json"
    try:
        if invocation_path.is_symlink() or not invocation_path.is_file():
            raise OSError
        invocation_bytes = invocation_path.read_bytes()
        invocation = json.loads(invocation_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BenchmarkWorkbenchError(
            "benchmark Codex invocation evidence is invalid"
        ) from exc
    argv = invocation.get("argv") if isinstance(invocation, dict) else None
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise BenchmarkWorkbenchError("benchmark Codex invocation evidence is invalid")
    schema_path = agent_artifact / "benchmark_output_schema.json"
    output_path = agent_artifact / "benchmark_response.json"
    required_values = {
        "--output-schema": str(schema_path),
        "--output-last-message": str(output_path),
        "--add-dir": str(agent_artifact),
        "--cd": str(agent_artifact),
        "-m": str(runtime_evidence.get("model") or ""),
        "-c": f'model_reasoning_effort={runtime_evidence.get("model_reasoning_effort")}',
    }
    if (
        invocation.get("schema_version")
        != "quality-benchmark-codex-invocation-v1"
        or not argv
        or argv[0] != str(runtime_evidence.get("codex_command") or "")
        or "exec" not in argv
        or "--json" not in argv
        or "--skip-git-repo-check" not in argv
        or "--ignore-user-config" not in argv
        or "--ignore-rules" not in argv
        or "--dangerously-bypass-approvals-and-sandbox" not in argv
    ):
        raise BenchmarkWorkbenchError("benchmark Codex invocation evidence is invalid")
    for flag, expected_value in required_values.items():
        positions = [index for index, item in enumerate(argv) if item == flag]
        if (
            len(positions) != 1
            or positions[0] + 1 >= len(argv)
            or argv[positions[0] + 1] != expected_value
        ):
            raise BenchmarkWorkbenchError(
                "benchmark Codex invocation evidence is invalid"
            )
    if argv[-4:] != [
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
    ]:
        raise BenchmarkWorkbenchError("benchmark Codex invocation evidence is invalid")
    return hashlib.sha256(invocation_bytes).hexdigest()


def _write_runtime_evidence(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _benchmark_workflow(
    *,
    prompt: str,
    output_schema: Mapping[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    return {
        "id": "quality-benchmark-generation-v1",
        "name": "Independent quality benchmark generation",
        "version": 1,
        "inputs": [
            {
                "id": "benchmark_request",
                "type": "free_text",
                "required": True,
                "role": "requirement",
            }
        ],
        "execution_profiles": [
            {
                "id": "rapid",
                "label": "Rapid",
                "delivery_class": "bounded_analysis",
                "expected_duration_minutes": [5, 15],
                "max_subagents": 1,
            },
            {
                "id": "deep",
                "label": "Deep",
                "delivery_class": "full_test_delivery",
                "expected_duration_minutes": [15, 90],
                "max_subagents": 1,
            },
        ],
        "steps": [
            {
                "id": "analyze",
                "type": "agent_task",
                "provider": _BENCHMARK_CODEX_PROVIDER,
                "goal": prompt,
                "required_artifacts": ["benchmark_response.json"],
                "timeout_sec": timeout_seconds,
                "idle_timeout_sec": timeout_seconds,
            }
        ],
        "outputs": [
            {
                "id": "benchmark_response",
                "type": "json",
                "from": "analyze",
                "artifact": "benchmark_response.json",
                "schema": dict(output_schema),
            }
        ],
    }


def _load_repair_summary(task_artifact: Path, agent_artifact: Path) -> dict[str, Any]:
    for path in (
        task_artifact / "quality_repair_summary.json",
        agent_artifact / "quality_repair_summary.json",
        task_artifact / "repair_summary.json",
    ):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and "successful_attempt_count" in value:
            try:
                successful_attempt_count = max(
                    0, int(value["successful_attempt_count"])
                )
            except (TypeError, ValueError) as exc:
                raise BenchmarkWorkbenchError(
                    "workbench repair summary is invalid"
                ) from exc
            return {
                **value,
                "attempt_count": successful_attempt_count,
            }
    final_audit = _read_mapping(task_artifact / "test_activity_quality_audit.json")
    external_repair = final_audit.get("external_agent_quality_repair")
    if isinstance(external_repair, dict) and external_repair.get("accepted") is True:
        return {"attempt_count": 1, "terminal_block_reason": None}
    return {"attempt_count": 0, "terminal_block_reason": None}


def _load_repair_audit(task_artifact: Path, agent_artifact: Path) -> dict[str, Any]:
    """Project the staged repair trace without retaining model or row content."""

    for path in (
        agent_artifact / "quality_repair_result.json",
        task_artifact / "quality_repair_result.json",
    ):
        if not path.is_file():
            continue
        value = _read_mapping(path)
        attempts = value.get("attempts")
        if not isinstance(attempts, list):
            raise BenchmarkWorkbenchError("workbench repair audit is invalid")
        try:
            attempted_count = max(0, int(value.get("attempt_count") or 0))
            remaining_seconds = max(
                0.0, float(value.get("remaining_seconds") or 0.0)
            )
        except (TypeError, ValueError) as exc:
            raise BenchmarkWorkbenchError("workbench repair audit is invalid") from exc
        if attempted_count != len(attempts):
            raise BenchmarkWorkbenchError("workbench repair audit is inconsistent")
        outcomes: list[dict[str, Any]] = []
        for index, raw_attempt in enumerate(attempts, start=1):
            if not isinstance(raw_attempt, dict):
                raise BenchmarkWorkbenchError("workbench repair audit is invalid")
            try:
                outcome = {
                    "attempt": int(raw_attempt.get("attempt") or index),
                    "accepted": raw_attempt.get("accepted") is True,
                    "status_before": str(raw_attempt.get("status_before") or ""),
                    "status_after": str(raw_attempt.get("status_after") or ""),
                    "issues_before": max(
                        0, int(raw_attempt.get("issues_before") or 0)
                    ),
                    "issues_after": max(
                        0, int(raw_attempt.get("issues_after") or 0)
                    ),
                }
            except (TypeError, ValueError) as exc:
                raise BenchmarkWorkbenchError(
                    "workbench repair audit is invalid"
                ) from exc
            outcomes.append(outcome)
        return {
            "attempted_count": attempted_count,
            "accepted_count": sum(1 for item in outcomes if item["accepted"]),
            "last_accepted_attempt": max(
                (
                    int(item["attempt"])
                    for item in outcomes
                    if item["accepted"]
                ),
                default=0,
            ),
            "stopped_reason": str(value.get("stopped_reason") or ""),
            "remaining_seconds": remaining_seconds,
            "outcomes": outcomes,
        }
    return {
        "attempted_count": 0,
        "accepted_count": 0,
        "last_accepted_attempt": 0,
        "stopped_reason": "",
        "remaining_seconds": 0.0,
        "outcomes": [],
    }


def _validated_final_provenance(
    *,
    task_artifact: Path,
    response_path: Path,
    repair_attempt_count: int,
    expected_status: str,
) -> dict[str, Any]:
    outputs_path = task_artifact / "workflow_outputs.json"
    outputs_payload = _read_mapping(outputs_path)
    if str(outputs_payload.get("status") or "") != str(expected_status):
        raise BenchmarkWorkbenchError("workflow output status does not match execution")
    response_bytes = response_path.read_bytes()
    response_sha256 = hashlib.sha256(response_bytes).hexdigest()
    outputs = outputs_payload.get("outputs")
    if not isinstance(outputs, list):
        raise BenchmarkWorkbenchError("validated workflow output contract is unavailable")
    accepted = next(
        (
            item
            for item in outputs
            if isinstance(item, dict)
            and str(item.get("id") or "") == "benchmark_response"
            and str(item.get("artifact") or "") == "benchmark_response.json"
            and str(item.get("status") or "") == "ok"
        ),
        None,
    )
    if not accepted or str(accepted.get("sha256") or "") != response_sha256:
        raise BenchmarkWorkbenchError("final benchmark response failed output validation")
    return {
        "attempt": repair_attempt_count,
        "event": "workflow_output_validated",
        "response_sha256": response_sha256,
        "workflow_outputs_sha256": hashlib.sha256(outputs_path.read_bytes()).hexdigest(),
    }


def _read_mapping(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _require_remaining(deadline_monotonic: float) -> float:
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("quality benchmark absolute deadline exceeded")
    return remaining
