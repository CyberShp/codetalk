"""Independent, source-bound semantic judgments for quality benchmarks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Mapping, Sequence

from app.services.agent_sandbox import benchmark_agent_sandbox
from app.services.behavior_claim_validator import (
    build_behavior_claim_audit_prompt,
    normalize_behavior_claim_verdicts,
)
from app.services.harness_facade import AgentHarnessFacade, HarnessRunRequest


SemanticVerdict = Literal["supports", "contradicts", "insufficient"]
SemanticAxis = Literal["accuracy", "breadth", "depth"]
JUDGE_VERSION = "quality-semantic-judge-v1"
AUDIT_SCHEMA_VERSION = "quality-semantic-judge-audit-v1"
DEFAULT_JUDGE_MODEL = "gpt-5.5"
_LIMIT_DEADLINE = "SEMANTIC_JUDGE_DEADLINE_EXCEEDED"
_LIMIT_UNAVAILABLE = "SEMANTIC_JUDGE_UNAVAILABLE"
_LIMIT_NON_INDEPENDENT = "SEMANTIC_JUDGE_NOT_INDEPENDENT"
_VERDICTS = frozenset({"supports", "contradicts", "insufficient"})
_EVIDENCE_REF = re.compile(
    r"^(?P<scheme>source|test)://(?P<path>[^#]+)#"
    r"(?:(?P<symbol>[^:]+):)?L(?P<start>[0-9]+)-L(?P<end>[0-9]+)"
    r"(?::(?P<label>[^#]+))?$"
)


@dataclass(frozen=True)
class SemanticJudgment:
    judgment_id: str
    axis: SemanticAxis
    candidate_statement: str
    oracle_statement: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class SemanticJudgeResult:
    verdicts: dict[str, SemanticVerdict]
    metadata: dict[str, Any]
    limitations: tuple[str, ...]


Materializer = Callable[..., Awaitable[dict[str, Any]]]


class CodexHarnessSemanticMaterializer:
    """Run the benchmark judge through CodeTalk's isolated Codex harness."""

    def __init__(
        self,
        *,
        facade_factory: Callable[[Path], Any] = AgentHarnessFacade,
        codex_resolver: Callable[[], str | None] | None = None,
        cli_version_loader: Callable[[str], str] | None = None,
        sandbox_factory: Callable[..., Any] = benchmark_agent_sandbox,
        approved_network_targets: tuple[str, ...] | None = None,
    ) -> None:
        self._facade_factory = facade_factory
        self._codex_resolver = codex_resolver or (lambda: shutil.which("codex"))
        self._cli_version_loader = cli_version_loader or _codex_cli_version
        self._sandbox_factory = sandbox_factory
        self._approved_network_targets = (
            approved_network_targets
            if approved_network_targets is not None
            else _configured_network_targets()
        )

    async def __call__(
        self,
        *,
        request: dict[str, Any],
        repo_path: str | Path,
        generator_identity: str,
        timeout_seconds: float,
        judge_model: str,
        mode: str,
        deadline_monotonic: float,
    ) -> dict[str, Any]:
        source = Path(repo_path).resolve(strict=True)
        command = str(self._codex_resolver() or "").strip()
        if not command:
            raise RuntimeError("Codex CLI is unavailable")
        cli_version = self._cli_version_loader(command)
        effort = "high" if mode == "deep" else "low"
        validator = {
            "provider": "openai",
            "runtime_id": "quality-benchmark-codex-cli",
            "model": str(judge_model),
            "reasoning_effort": effort,
            "independent": _model_key(judge_model) != _model_key(generator_identity),
            "cli_version": cli_version,
        }
        remaining = min(
            max(0.0, float(timeout_seconds)),
            max(0.0, deadline_monotonic - time.monotonic()),
        )
        if remaining <= 0:
            raise TimeoutError("semantic judge deadline exhausted")

        with tempfile.TemporaryDirectory(prefix="codetalk-quality-semantic-judge-") as root:
            artifact_dir = Path(root).resolve()
            facade = self._facade_factory(artifact_dir)
            prompt = _benchmark_semantic_prompt(request)
            task_bundle = {
                "rendered_input": prompt,
                "validation_request": request,
                "required_artifacts": ["semantic_verdicts.json"],
                "execution_contract": {
                    "outputs": {
                        "declared_outputs": [
                            {
                                "output_id": "semantic_verdicts",
                                "artifact": "semantic_verdicts.json",
                                "required": True,
                            }
                        ]
                    }
                },
            }
            run_request = HarnessRunRequest(
                provider="codex",
                command=[
                    command,
                    "-m",
                    str(judge_model),
                    "-c",
                    f'model_reasoning_effort="{effort}"',
                    "exec",
                    "--ephemeral",
                    "--ignore-rules",
                    "--skip-git-repo-check",
                    "-s",
                    "read-only",
                ],
                cwd=str(source),
                workflow_snapshot={
                    "id": "quality-benchmark-semantic-judge",
                    "version": 1,
                    "name": "Independent quality semantic judge",
                },
                task_bundle=task_bundle,
                prompt_transport="codex_exec_json",
                timeout_seconds=max(1, int(math.ceil(remaining))),
                idle_timeout_seconds=min(120.0, max(1.0, remaining)),
                requires_network=True,
                run_id=(
                    "quality_semantic_judge_"
                    + hashlib.sha256(
                        f"{request.get('request_sha256')}:{time.monotonic_ns()}".encode()
                    ).hexdigest()[:16]
                ),
            )
            with self._sandbox_factory(
                source_dir=source,
                model=str(judge_model),
                mode=mode,
                approved_network_targets=self._approved_network_targets,
            ):
                session = facade.prepare(run_request)
                result = facade.execute(
                    session.run_id,
                    timeout_sec=max(1, int(math.ceil(remaining))),
                    idle_timeout_sec=min(120.0, max(1.0, remaining)),
                    is_cancelled=lambda: time.monotonic() >= deadline_monotonic,
                )
            if bool(getattr(result, "timed_out", False)):
                raise TimeoutError("semantic judge harness timed out")
            if str(getattr(result, "status", "")) != "completed" or getattr(
                result, "exit_code", None
            ) != 0:
                raise RuntimeError("semantic judge harness did not complete")
            output_path = artifact_dir / "semantic_verdicts.json"
            try:
                raw_output = output_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise RuntimeError("semantic judge artifact is unavailable") from exc
            normalized = normalize_behavior_claim_verdicts(
                raw_output=raw_output,
                request=request,
                validator=validator,
            )
            sandbox_path = artifact_dir / "sandbox_policy.json"
            normalized["response_models"] = [str(judge_model)]
            normalized["harness"] = {
                "session_id": str(getattr(result, "session_id", "")),
                "status": str(getattr(result, "status", "")),
                "sandbox_policy_sha256": _optional_file_sha256(sandbox_path),
            }
            return normalized


class BehaviorClaimBatchSemanticJudge:
    """Adapt benchmark observations to the mature source-bound L2 validator."""

    def __init__(
        self,
        *,
        materializer: Materializer | None = None,
        judge_model: str = DEFAULT_JUDGE_MODEL,
    ) -> None:
        self._materializer = materializer or CodexHarnessSemanticMaterializer()
        self.judge_model = str(judge_model).strip() or DEFAULT_JUDGE_MODEL

    def judge(
        self,
        *,
        judgments: Sequence[SemanticJudgment],
        source_dir: str | Path,
        generator_model: str,
        deadline_monotonic: float,
        snapshot_label: str,
        mode: str = "rapid",
        judge_model: str | None = None,
    ) -> SemanticJudgeResult:
        started = time.monotonic()
        ordered = tuple(judgments)
        ids = tuple(item.judgment_id for item in ordered)
        effective_judge_model = str(judge_model or self.judge_model).strip()
        if len(set(ids)) != len(ids) or any(not item_id for item_id in ids):
            raise ValueError("semantic judgment ids must be non-empty and unique")
        if not ordered:
            return SemanticJudgeResult(
                verdicts={},
                metadata=_audit_metadata(
                    snapshot_label=snapshot_label,
                    status="completed",
                    request_sha256=_sha256_json({"claims": []}),
                    result_sha256=_sha256_json({"claims": []}),
                    validator={
                        "provider": "openai",
                        "runtime_id": "quality-benchmark-codex-cli",
                        "model": effective_judge_model,
                        "reasoning_effort": "high" if mode == "deep" else "low",
                        "independent": True,
                    },
                    duration_ms=0.0,
                ),
                limitations=(),
            )
        if time.monotonic() >= deadline_monotonic:
            return _failed_result(
                ids=ids,
                snapshot_label=snapshot_label,
                status="timed_out",
                limitation=_LIMIT_DEADLINE,
                judge_model=effective_judge_model,
                mode=mode,
                started=started,
            )
        if _model_key(generator_model) == _model_key(effective_judge_model):
            return _failed_result(
                ids=ids,
                snapshot_label=snapshot_label,
                status="non_independent",
                limitation=_LIMIT_NON_INDEPENDENT,
                judge_model=effective_judge_model,
                mode=mode,
                started=started,
            )

        try:
            request = _build_validation_request(ordered, Path(source_dir))
        except (OSError, UnicodeError, ValueError):
            return _failed_result(
                ids=ids,
                snapshot_label=snapshot_label,
                status="unavailable",
                limitation=_LIMIT_UNAVAILABLE,
                judge_model=effective_judge_model,
                mode=mode,
                started=started,
            )
        remaining = max(0.0, deadline_monotonic - time.monotonic())
        if remaining <= 0:
            return _failed_result(
                ids=ids,
                snapshot_label=snapshot_label,
                status="timed_out",
                limitation=_LIMIT_DEADLINE,
                judge_model=effective_judge_model,
                mode=mode,
                started=started,
                request_sha256=str(request["request_sha256"]),
            )
        try:
            result = asyncio.run(
                asyncio.wait_for(
                    self._materializer(
                        request=request,
                        repo_path=Path(source_dir).resolve(),
                        generator_identity=f"agent-runtime:codex:{generator_model}",
                        timeout_seconds=remaining,
                        judge_model=effective_judge_model,
                        mode=mode,
                        deadline_monotonic=deadline_monotonic,
                    ),
                    timeout=remaining,
                )
            )
        except (TimeoutError, asyncio.TimeoutError):
            return _failed_result(
                ids=ids,
                snapshot_label=snapshot_label,
                status="timed_out",
                limitation=_LIMIT_DEADLINE,
                judge_model=effective_judge_model,
                mode=mode,
                started=started,
                request_sha256=str(request["request_sha256"]),
            )
        except (OSError, RuntimeError, ValueError):
            return _failed_result(
                ids=ids,
                snapshot_label=snapshot_label,
                status="unavailable",
                limitation=_LIMIT_UNAVAILABLE,
                judge_model=effective_judge_model,
                mode=mode,
                started=started,
                request_sha256=str(request["request_sha256"]),
            )
        if time.monotonic() >= deadline_monotonic:
            return _failed_result(
                ids=ids,
                snapshot_label=snapshot_label,
                status="timed_out",
                limitation=_LIMIT_DEADLINE,
                judge_model=effective_judge_model,
                mode=mode,
                started=started,
                request_sha256=str(request["request_sha256"]),
            )

        validator = result.get("validator") if isinstance(result, Mapping) else {}
        validator = dict(validator) if isinstance(validator, Mapping) else {}
        response_models = tuple(
            str(value).strip()
            for value in result.get("response_models") or []
            if str(value).strip()
        ) if isinstance(result, Mapping) else ()
        independent = bool(validator.get("independent"))
        validator_model = str(validator.get("model") or "").strip()
        if (
            str(result.get("status") or "") != "completed"
            or not str(validator.get("provider") or "").strip()
            or not validator_model
        ):
            return _failed_result(
                ids=ids,
                snapshot_label=snapshot_label,
                status="unavailable",
                limitation=_LIMIT_UNAVAILABLE,
                judge_model=validator_model or effective_judge_model,
                mode=mode,
                started=started,
                request_sha256=str(request["request_sha256"]),
                validator=validator,
            )
        same_model = _model_key(validator_model) == _model_key(generator_model) or any(
            _model_key(model) == _model_key(generator_model) for model in response_models
        )
        if same_model or not independent:
            return _failed_result(
                ids=ids,
                snapshot_label=snapshot_label,
                status="non_independent",
                limitation=_LIMIT_NON_INDEPENDENT,
                judge_model=validator_model or effective_judge_model,
                mode=mode,
                started=started,
                request_sha256=str(request["request_sha256"]),
                validator=validator,
            )
        by_id = {
            str(item.get("claim_id") or ""): str(item.get("status") or "")
            for item in result.get("claims") or []
            if isinstance(item, Mapping)
        }
        verdicts: dict[str, SemanticVerdict] = {
            item_id: (
                by_id[item_id]  # type: ignore[assignment]
                if by_id.get(item_id) in _VERDICTS
                else "insufficient"
            )
            for item_id in ids
        }
        result_sha256 = _sha256_json(result)
        return SemanticJudgeResult(
            verdicts=verdicts,
            metadata=_audit_metadata(
                snapshot_label=snapshot_label,
                status="completed",
                request_sha256=str(request["request_sha256"]),
                result_sha256=result_sha256,
                validator=validator,
                duration_ms=(time.monotonic() - started) * 1000,
                harness=(
                    dict(result.get("harness") or {})
                    if isinstance(result.get("harness"), Mapping)
                    else None
                ),
            ),
            limitations=(),
        )


def _build_validation_request(
    judgments: Sequence[SemanticJudgment], source_dir: Path
) -> dict[str, Any]:
    source = source_dir.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("semantic judge source boundary is not a directory")
    contexts: list[dict[str, Any]] = []
    context_by_ref: dict[tuple[str, int, int], dict[str, Any]] = {}
    claims: list[dict[str, Any]] = []
    for judgment in judgments:
        context_ids: list[str] = []
        bindings: list[dict[str, str]] = []
        if not judgment.candidate_statement.strip() or not judgment.oracle_statement.strip():
            raise ValueError("semantic judgment statements must be non-empty")
        for raw_ref in judgment.evidence_refs:
            parsed = _parse_evidence_ref(raw_ref)
            key = (parsed["path"], parsed["start_line"], parsed["end_line"])
            context = context_by_ref.get(key)
            if context is None:
                context = _source_context(source, **parsed)
                context = {"context_id": f"CTX-{len(contexts) + 1:04d}", **context}
                context_by_ref[key] = context
                contexts.append(context)
            context_ids.append(str(context["context_id"]))
            bindings.append(
                {
                    "path": parsed["path"],
                    "symbol": parsed["symbol"],
                    "lines": f"L{parsed['start_line']}-L{parsed['end_line']}",
                    "quote": str(context["content"]),
                }
            )
        claims.append(
            {
                "claim_id": judgment.judgment_id,
                "type": "source_behavior",
                "artifact": "quality_benchmark",
                "row_id": judgment.judgment_id,
                "statement": (
                    "BENCHMARK_CANDIDATE: "
                    f"{judgment.candidate_statement.strip()}\n"
                    "HIDDEN_ORACLE_REQUIREMENT: "
                    f"{judgment.oracle_statement.strip()}"
                ),
                "benchmark_axis": judgment.axis,
                "candidate_statement": judgment.candidate_statement.strip(),
                "oracle_statement": judgment.oracle_statement.strip(),
                "binding": judgment.judgment_id,
                "context_ids": list(dict.fromkeys(context_ids)),
                "evidence_bindings": bindings,
            }
        )
    payload: dict[str, Any] = {
        "kind": "behavior_claim_validation_request",
        "schema_version": 2,
        "repo_path": str(source),
        "claims": claims,
        "contexts": contexts,
        "candidate_count": len(claims),
        "requested_count": len(claims),
        "truncated": False,
    }
    payload["request_sha256"] = _sha256_json(payload)
    return payload


def _parse_evidence_ref(raw_ref: str) -> dict[str, Any]:
    match = _EVIDENCE_REF.fullmatch(str(raw_ref).strip())
    if match is None:
        raise ValueError("semantic judge evidence must be one exact source range")
    start = int(match.group("start"))
    end = int(match.group("end"))
    if start < 1 or end < start:
        raise ValueError("semantic judge evidence range is invalid")
    return {
        "path": match.group("path").lstrip("/"),
        "symbol": str(match.group("symbol") or match.group("label") or ""),
        "start_line": start,
        "end_line": end,
    }


def materialize_semantic_evidence_ref(
    raw_ref: str,
    source_dir: str | Path,
) -> dict[str, Any]:
    """Parse and materialize one exact source/test range inside a pinned source."""

    source = Path(source_dir).resolve(strict=True)
    if not source.is_dir():
        raise ValueError("semantic evidence source boundary is not a directory")
    return _source_context(source, **_parse_evidence_ref(raw_ref))


def _source_context(
    source: Path,
    *,
    path: str,
    symbol: str,
    start_line: int,
    end_line: int,
) -> dict[str, Any]:
    candidate = (source / path).resolve(strict=True)
    if not candidate.is_file() or not candidate.is_relative_to(source):
        raise ValueError("semantic judge evidence escapes the source boundary")
    lines = candidate.read_text(encoding="utf-8", errors="strict").splitlines()
    if end_line > len(lines):
        raise ValueError("semantic judge evidence range exceeds source file")
    content = "\n".join(lines[start_line - 1 : end_line])
    return {
        "path": path,
        "symbol": symbol,
        "start_line": start_line,
        "end_line": end_line,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "content": content,
    }


def _benchmark_semantic_prompt(request: dict[str, Any]) -> str:
    benchmark_rules = "\n".join(
        [
            "BENCHMARK SEMANTIC EQUIVALENCE RULES:",
            "Each source_behavior statement contains BENCHMARK_CANDIDATE and "
            "HIDDEN_ORACLE_REQUIREMENT.",
            "Return supports only when the candidate fully and correctly expresses the "
            "oracle requirement and the cited source directly supports that complete meaning.",
            "A natural paraphrase may support. A reversed, false, overbroad, or partial "
            "candidate must be contradicts or insufficient even when its source range is real.",
            "Write the final JSON object to semantic_verdicts.json in the declared artifact "
            "directory. Do not modify the source repository.",
        ]
    )
    return f"{benchmark_rules}\n\n{build_behavior_claim_audit_prompt(request)}"


def _failed_result(
    *,
    ids: Sequence[str],
    snapshot_label: str,
    status: str,
    limitation: str,
    judge_model: str,
    mode: str,
    started: float,
    request_sha256: str = "",
    validator: Mapping[str, Any] | None = None,
) -> SemanticJudgeResult:
    judge = dict(validator or {})
    judge.setdefault("provider", "openai")
    judge.setdefault("runtime_id", "quality-benchmark-codex-cli")
    judge.setdefault("model", judge_model)
    judge.setdefault("reasoning_effort", "high" if mode == "deep" else "low")
    judge["independent"] = False if status == "non_independent" else bool(
        judge.get("independent", False)
    )
    verdicts = {item_id: "insufficient" for item_id in ids}
    return SemanticJudgeResult(
        verdicts=verdicts,
        metadata=_audit_metadata(
            snapshot_label=snapshot_label,
            status=status,
            request_sha256=request_sha256 or _sha256_json({"judgment_ids": list(ids)}),
            result_sha256=_sha256_json(verdicts),
            validator=judge,
            duration_ms=(time.monotonic() - started) * 1000,
        ),
        limitations=(limitation,),
    )


def _audit_metadata(
    *,
    snapshot_label: str,
    status: str,
    request_sha256: str,
    result_sha256: str,
    validator: Mapping[str, Any],
    duration_ms: float,
    harness: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    allowed_judge_keys = (
        "provider",
        "runtime_id",
        "model",
        "reasoning_effort",
        "independent",
        "cli_version",
    )
    metadata = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "snapshot": str(snapshot_label),
        "status": str(status),
        "judge_version": JUDGE_VERSION,
        "judge_contract_sha256": hashlib.sha256(
            f"{JUDGE_VERSION}:behavior-claim-validator-v2:full-entailment".encode()
        ).hexdigest(),
        "judge": {
            key: validator[key]
            for key in allowed_judge_keys
            if key in validator
        },
        "request_sha256": str(request_sha256),
        "result_sha256": str(result_sha256),
        "duration_ms": round(max(0.0, duration_ms), 3),
    }
    if harness:
        metadata["harness"] = {
            key: harness[key]
            for key in ("session_id", "status", "sandbox_policy_sha256")
            if key in harness
        }
    return metadata


def _model_key(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    for prefix in (
        "agent-runtime:codex:",
        "agent-runtime:",
        "builtin-llm:",
        "openai:",
        "codex:",
    ):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
    return normalized


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _codex_cli_version(command: str) -> str:
    completed = subprocess.run(
        [command, "--version"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )
    version = (completed.stdout or completed.stderr).strip()
    if completed.returncode != 0 or not version:
        raise RuntimeError("Codex CLI version is unavailable")
    return version[:200]


def _configured_network_targets() -> tuple[str, ...]:
    raw = os.environ.get("CODETALK_QUALITY_BENCHMARK_NETWORK_TARGETS", "").strip()
    if not raw:
        return ()
    if raw.startswith("["):
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("benchmark network target configuration is invalid") from exc
        if not isinstance(values, list):
            raise ValueError("benchmark network target configuration is invalid")
        return tuple(str(item).strip() for item in values if str(item).strip())
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _optional_file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""
