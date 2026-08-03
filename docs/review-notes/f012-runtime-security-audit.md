---
feature_ids:
  - F012
topics:
  - runtime-reliability
  - privacy
  - benchmark-security
doc_kind: review-note
created: 2026-08-03
reviewed: 2026-08-03
reviewer: R4
review_effort: high
verdict: ACCEPT
baseline_status: pending
---

# F012 Runtime Reliability & Privacy Re-review

## Verdict

**ACCEPT for the limited R4 runtime/security re-review scope.** All three prior P1 findings and the prior P2 truth-boundary finding are closed by production call-chain evidence and focused regressions. No new release-blocking runtime or privacy finding was confirmed.

This ACCEPT does not approve F012 release by itself. The quality baseline remains `pending` and independently blocks release until the representative corpus runs and calibration evidence are frozen and audited.

## Scope

This re-review was intentionally limited to the four findings from the previous R4 REJECT and their direct regressions:

1. `quality_blocked` fail-closed propagation.
2. V3/public `timed_out` preservation.
3. Production invocation of `validate_truth_isolation` before Workbench/model execution.
4. Cold-under-five-minute structured work-sufficiency diagnosis, real external-Agent continuation, and manifest persistence.

Implementation and tests were not modified by R4.

## Commands And Evidence

### Prior runtime/security suite

```text
cd backend
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_quality_auto_repair.py \
  tests/test_quality_truth_isolation.py \
  tests/test_quality_benchmark_generator.py \
  tests/test_quality_evaluations_api.py \
  tests/test_quality_benchmark_runner.py \
  tests/test_quality_depth_evaluator.py

206 passed in 12.46s
```

The same suite produced 202 passes in the initial review. The four additional regressions are green without weakening the existing truth isolation, sandbox, failure-state, public-redaction, and evaluator-owned Depth checks.

### Runtime status and repair regressions

```text
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_workbench_task_run.py tests/test_agent_workbench_api.py tests/test_config.py \
  -k 'quality_repair or workflow_deadline or fast_result or quality_blocked or quality_repairing or total_execution_deadline or timeout'

35 passed, 396 deselected in 3.77s
```

The two timeout tests that failed in the initial review now pass. A narrower four-finding selection also passed:

```text
14 passed, 264 deselected in 2.04s
```

That selection covered the terminal Workbench status matrix, production truth rejection before invocation, cold-fast benchmark decisions, managed-Codex Workbench smoke, timeout/expiry races, and manifest regressions.

### Retained formal Workbench continuation smoke

```text
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_quality_benchmark_generator.py::test_benchmark_workbench_uses_builtin_managed_codex_non_tty_exec_json \
  --basetemp=/tmp/f012-r4-retest-workbench

1 passed in 1.70s
```

The fake Codex response was deliberately shallow and completed in under five minutes. Inspection of the retained formal TaskRun proved actual continuation rather than marker-only behavior:

```text
initial work-sufficiency status: insufficient
initial elapsed_seconds: 0.655
failed minimums: claims, breadth candidates/scenarios, depth nodes, evidence refs
quality_repairs/attempt_1/quality_audit_before.json: present
external_agent_quality_repair.attempted: true
external_agent_quality_repair.execution_status: completed
external_agent_quality_repair.validation_status: ok
external_agent_quality_repair.repair_artifacts: [benchmark_response.json]
external_agent_quality_repair.accepted: false
external_agent_quality_repair.reason: no_quality_progress
final workflow status: quality_blocked
```

The second Harness turn returned the same shallow candidate. The runner re-audited it, detected no quality progress, restored the accepted bytes, and ended `quality_blocked`. This is direct evidence of a bounded repair attempt plus fail-closed rollback, not merely a diagnostic file or event.

### Manifest persistence probe

```text
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_quality_benchmark_generator.py::test_generator_executes_through_workbench_and_publishes_content_hash_manifest \
  --basetemp=/tmp/f012-r4-retest-generator-manifest

1 passed in 0.17s
```

The retained `generation_manifest.json` contained `workbench_status=completed` and a structured `work_sufficiency` object. Production `_benchmark_execution_manifest()` preserved:

```json
{
  "cache_reuse": false,
  "generation_wall_clock_seconds": 0.013,
  "profile": "rapid",
  "work_sufficiency": "sufficient",
  "work_sufficiency_diagnostic": {
    "auto_continue": false,
    "elapsed_seconds": 10.0,
    "reasons": [],
    "status": "sufficient"
  },
  "workbench_status": "completed"
}
```

## Finding Closure

### P0

No P0 finding was confirmed.

### P1

No P1 finding remains open in this re-review scope.

#### Closed P1-1: `quality_blocked` can be published and lose its terminal state

- `quality_benchmark_generator.py:130-145` accepts only `completed`, `completed_empty`, and `needs_review`; every other Workbench status is converted to immutable failure evidence.
- `_workbench_status_failure()` preserves `quality_blocked`, `timed_out`, `cancelled`, `invalid`, `error`, and `failed` rather than collapsing them into success.
- The parameterized terminal-status matrix confirms `quality_blocked -> status=quality_blocked, failure_code=workbench_quality_blocked`, with no candidate snapshots published.
- `quality_benchmark_runner.py:857-877` now requires and propagates `workbench_status`, rejecting any non-deliverable value before report execution.
- The retained shallow continuation smoke ended `quality_blocked`; it did not appear as completed/pass after unsuccessful repair.

Closure: **confirmed**.

#### Closed P1-2: cold under-five-minute runs are marker-only

- `_apply_benchmark_work_sufficiency()` derives profile-specific evidence minimums from all three candidate axes without access to hidden truth: claims, breadth candidates/scenarios, depth nodes/edges/checks, distinct evidence references, and provider invocation.
- `_audit_test_activity_quality_before_deadline()` writes `benchmark_work_sufficiency.json`, marks insufficient work `needs_rework`, and feeds the resulting `benchmark_response.json` issue into the existing external-Agent targeted repair path.
- The retained formal Workbench smoke proves `AgentHarnessFacade.execute()` was reached for `quality_repairs/attempt_1`, its artifact contract was validated, and the result was independently re-audited.
- No-progress repair was rejected and rolled back; successful/sufficient runs can proceed, while unresolved insufficiency becomes `quality_blocked` and the generator fails closed.
- Generation and evaluation execution manifests preserve the structured diagnostic for deliverable runs.

Closure: **confirmed**.

#### Closed P1-3: timeout is projected as generic failure

- `_task_run_ui_status()` now handles `timed_out`/`timeout` before failed-node projection.
- `_task_run_ui_status_label()` exposes the corresponding timeout label.
- The live expiry monitor test, expiry-versus-cancel race test, and explicit failed-node precedence regression all pass.

Closure: **confirmed**.

### P2

No P2 finding remains open in this re-review scope.

#### Closed P2-1: truth-surface validator is not wired into production generation

- `generate_quality_benchmark_artifacts()` now requires explicit `truth_paths` and calls `validate_truth_isolation()` before `execute_quality_benchmark_workbench()`.
- The validated surface set includes task input, prompt capture, retrieval index, bundle/schema/network targets, and generator manifest projection.
- The production runner obtains the four truth artifact paths from the registered case package and passes them into generation.
- `test_generator_rejects_truth_leak_before_workbench_invocation` confirms a truth-bearing surface fails before the Workbench callable can run.
- Existing encoded, normalized, path-variant, OS sandbox, network, public API, and external-case-copy isolation regressions remain green.

Closure: **confirmed**.

## Release Decision

R4 runtime/security review no longer blocks F012. Release remains blocked by `baseline_status: pending`; that separate gate must provide representative rapid/deep timing, under-five-minute outcomes, multi-domain quality results, and frozen calibration evidence before final release approval.

## Repair Summary Contract Focused Re-review

### Latest Verdict

**REJECT.** The production filtering behavior is correct and fail-closed, but the submitted regression test does not cover the real failure boundary. This latest verdict supersedes the earlier R4 release decision only for the current `repair_summary` contract change; the four previously closed runtime/security findings remain closed.

Scope was limited to:

- `backend/app/services/quality_benchmark_runner.py`
- `backend/tests/test_quality_benchmark_runner.py`

### Evidence

```text
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_quality_benchmark_runner.py

40 passed in 0.13s
```

R4 also invoked the production `main()` boundary with five malformed `repair_summary.json` payloads while recording whether `run_quality_benchmark_case()` was reached:

```text
non-object JSON: ValueError; evaluator_called=false
missing attempt_count: ValidationError; evaluator_called=false
invalid attempt_count string: ValidationError; evaluator_called=false
negative attempt_count: ValidationError; evaluator_called=false
invalid terminal_block_reason object: ValidationError; evaluator_called=false
```

A positive production-CLI probe used a summary containing both provenance objects. The source file SHA-256 was unchanged after `main()`, its five keys remained present, and the evaluator received only:

```json
{
  "attempt_count": 1,
  "elapsed_seconds": 12.5,
  "terminal_block_reason": null
}
```

### Findings

#### Provenance preservation: confirmed

`main()` reads `repair_summary.json` and constructs a separate evaluator projection. It does not rewrite the source generation audit artifact. The positive probe retained `first_provenance` and `final_provenance` byte-for-byte while excluding them from the strict evaluator contract.

#### CLI filter boundary: confirmed fail-closed

`_evaluation_repair_summary()` first requires a JSON object, explicitly selects only the three evaluator-owned fields, and validates that projection with `RepairSummary`. Extra generation-audit fields cannot cross the boundary, while missing, malformed, negative, or wrong-typed contract fields stop execution before evaluation or public projection.

#### P1: regression test does not cover actual CLI rejection

The only new regression extends the successful CLI test with provenance fields and asserts the filtered evaluator payload. It does not supply any malformed summary, assert an exception, or prove that `run_quality_benchmark_case()` and `_publish_task_run_projection()` remain uncalled on rejection. Consequently, the code's fail-closed behavior is verified only by this independent ad hoc probe, not protected by the committed suite.

This is release-blocking for the focused change because its purpose is a trust-boundary contract repair and the requested real-failure regression is absent.

### Necessary Modification

Add a parameterized production-CLI regression covering at least non-object JSON, missing required fields, invalid/negative numeric fields, and invalid `terminal_block_reason`. Each case must assert failure before `run_quality_benchmark_case()` and `_publish_task_run_projection()` are called. Extend the positive provenance case to assert that the original `repair_summary.json` content or hash is unchanged after CLI filtering.

## Repair Summary Contract Final Re-review

### Final Verdict

**ACCEPT.** The previously required regression coverage is now present and green. This final verdict supersedes the focused REJECT immediately above; no release-blocking finding remains for the `repair_summary` CLI contract change.

### Current Diff Review

- The positive CLI test retains generation-only `first_provenance` and `final_provenance`, captures the original `repair_summary.json` bytes, verifies the evaluator receives only the strict three-field projection, and asserts the original bytes remain unchanged after evaluation and publication.
- The new parameterized test enters through production `main()` for seven invalid payload classes: non-object JSON, missing field, negative attempt count, negative elapsed time, forbidden string-to-integer coercion, empty terminal reason, and wrong terminal-reason type.
- Both `run_quality_benchmark_case()` and `_publish_task_run_projection()` are replaced by a fail sentinel. Every invalid payload raises before either trust-boundary consumer is reached.
- The related expired-deadline fix checks `remaining_seconds <= 0` before the minimum-remaining-time branch, preserving `workflow_deadline_exceeded` rather than misclassifying an expired deadline as merely insufficient time.

### Verification

```text
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_quality_benchmark_runner.py tests/test_quality_auto_repair.py

62 passed in 1.05s
```

```text
PYTHONPATH=. .venv/bin/python -m pytest -q -vv \
  tests/test_quality_benchmark_runner.py::test_cli_rejects_invalid_generator_repair_summary_before_evaluation \
  tests/test_quality_benchmark_runner.py::test_cli_case_selector_executes_runner_and_publishes_public_task_report \
  tests/test_quality_auto_repair.py::test_external_repair_records_an_expired_absolute_deadline

9 passed in 0.10s
```

The nine cases are the seven parameterized failures, the positive provenance/bytes-preservation path, and the expired-deadline priority regression.

```text
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_quality_auto_repair.py \
  tests/test_quality_truth_isolation.py \
  tests/test_quality_benchmark_generator.py \
  tests/test_quality_evaluations_api.py \
  tests/test_quality_benchmark_runner.py \
  tests/test_quality_depth_evaluator.py

214 passed in 12.25s
```

### Closure

1. Provenance remains complete in the generation audit artifact: **confirmed**.
2. The CLI projection is strict and fail-closed before evaluator/publication: **confirmed**.
3. Committed regressions now cover both the valid provenance-bearing path and representative real CLI failures: **confirmed**.

No further modification is required for this focused change. `baseline_status: pending` remains a separate F012 release gate.
