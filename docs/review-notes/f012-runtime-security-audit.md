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

## Formal Baseline Second-chain Focused Review

### Latest Verdict

**REJECT.** The runner root selection and generator repair-summary split are correct, but the baseline freezer does not yet provide a fail-closed all-file integrity anchor. Two independent tamper probes were accepted, and the advertised legacy root-anchor path rejects the legacy artifact layout before reaching its anchor check. This verdict supersedes the preceding focused ACCEPT for the current formal-baseline diff.

### Scope

- `backend/app/services/quality_benchmark_runner.py`
- `backend/app/services/quality_benchmark_generator.py`
- `backend/app/services/quality_baseline_freezer.py`
- Their three corresponding test files.

R4 did not modify implementation or tests.

### Automated Evidence

```text
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_quality_benchmark_runner.py \
  tests/test_quality_benchmark_generator.py \
  tests/test_quality_baseline_freezer.py

93 passed in 15.18s
```

The green suite is useful but does not exercise the tamper cases below.

### Confirmed Positive Behavior

#### Fresh single, fresh multi, and explicit replay roots

- Fresh single generation writes directly to `<output>.run-artifacts`, and evaluation reads that same immutable generator root.
- Fresh multi generation writes to `<output>.run-artifacts/<case_id>` and evaluation writes to `<output>/<case_id>`.
- Fresh generation never calls `_publish_task_run_projection`, so it does not try to mutate the generated read-only evidence root.
- Explicit single replay continues to support either a direct root containing `first_pass` or a case child root.
- Explicit multi replay reads `<run-artifacts>/<case_id>` and publishes the redacted public projection into each explicit task-run root.
- An independent production-`main()` two-case probe confirmed both fresh and replay layouts, with zero fresh projections and two explicit replay projections.

The committed diff adds a fresh-single regression but no fresh-multi or explicit-multi regression. The code paths worked in the independent probe; this remains a coverage gap rather than a confirmed path defect.

#### Strict evaluator repair summary and provenance retention

- Published `repair_summary.json` now contains exactly `attempt_count`, `elapsed_seconds`, and `terminal_block_reason`, matching the strict evaluator contract.
- `first_provenance` and `final_provenance` remain in `workbench_audit.json`, which is included in the generator hash manifest when present.
- The runner's strict projection remains fail-closed, and no hidden-truth field was added to the public projection or evaluator summary.

No immutability or truth-redaction regression was confirmed in these runner/generator changes.

### Findings

#### P1-1: a nested file named `artifact_hash_manifest.json` bypasses the generator file set

`quality_baseline_freezer.py:390-394` excludes every file whose basename is `artifact_hash_manifest.json`. Only the root control file should be excluded. A file at `first_pass/artifact_hash_manifest.json` is copied into the staged generator, omitted from `actual_files`, absent from the hashed descriptor set, and retained in the final read-only baseline.

Independent probe:

```text
added: first_pass/artifact_hash_manifest.json
listed in root hash manifest: false
freeze result: accepted
unmanifested file retained in baseline: true
```

This directly contradicts the all-file manifest requirement.

#### P1-2: the current filename reference is not an immutable root anchor

For current artifacts, `generation_manifest.json` only records `artifact_hash_manifest.json` by filename. The freezer verifies internal hash-manifest consistency but has no independent expected digest for that manifest or its `root_sha256`. An actor able to alter generator evidence can alter a candidate and recompute the descriptors/root; all current checks then pass.

Independent probe:

```text
modified: first_pass/candidate.json
recomputed: every artifact descriptor and root_sha256
generation anchor: "artifact_hash_manifest.json"
freeze result: accepted
tampered candidate retained in final baseline: true
```

A control probe that modified `workbench_audit.json` without recomputing the manifest correctly failed with `generator artifact hash mismatch`. The defect is therefore specifically the missing independent anchor, not the per-file comparison.

#### P1-3: the legacy root-anchor branch does not accept the legacy artifact layout

The legacy format hashes the first/final artifact trees and stores that root in `generation_manifest.artifact_root_sha256`. The new all-file set comparison runs first and requires top-level audit/manifests to appear in the hash descriptor set. A reconstructed valid legacy input failed with `generator artifact set does not match hash manifest`, so the fallback at lines 411-414 was never reached.

If instead the all-file set includes `generation_manifest.json`, embedding that same set's root digest inside `generation_manifest.json` creates a circular digest dependency. The current diff and tests provide no constructible successful legacy case and no valid/invalid legacy-anchor regression.

### Test Coverage Assessment

The corresponding tests are insufficient for the integrity claims:

- No test mutates a hashed generator file and asserts freeze rejection.
- No test mutates a file and recomputes the hash manifest while asserting rejection by an independent anchor.
- No test adds an unlisted nested same-basename control file.
- No test proves a valid legacy bundle is accepted and a wrong legacy root is rejected.
- No committed test covers fresh multi-case root selection or explicit multi-case replay projection.

### Necessary Modifications

1. Exclude only the root `artifact_hash_manifest.json` by exact relative path; every nested file, including a same-basename file, must appear in the descriptor set or cause rejection.
2. Bind the current generator root or hash-manifest digest to an independent immutable authority, such as the evaluation manifest produced for that run. A filename reference alone is not an integrity anchor.
3. Define a non-circular legacy contract. If legacy compatibility is required, validate its historical file set and root anchor explicitly; otherwise remove the unreachable fallback and reject legacy input clearly.
4. Add fail-closed tests for ordinary tamper, tamper plus recomputed manifest, extra/unlisted files, nested same-basename files, current anchor mismatch, and valid/invalid legacy anchors.
5. Add fresh multi and explicit multi replay regressions so the root/projection fix is protected across every stated CLI mode.

R4 remains blocking for this diff. `baseline_status: pending` is also still an independent release blocker.

## Formal Baseline Second-chain Final Re-review

### Final Verdict

**ACCEPT.** The three prior P1 findings are closed by the current implementation, committed regressions, and independent tamper probes. This verdict supersedes the immediately preceding formal-baseline REJECT. No release-blocking immutability or truth-redaction finding remains in this focused scope.

### Automated Evidence

```text
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_quality_benchmark_runner.py \
  tests/test_quality_benchmark_generator.py \
  tests/test_quality_baseline_freezer.py

104 passed in 17.34s
```

The focused changed-path selection, including the supplemental mutual-exclusion coverage, expanded to twelve pytest cases and passed:

```text
generator nested same-name inclusion: passed
fresh single root: passed
fresh multi plus explicit replay roots: passed
evaluation manifest generator-root binding: passed
ordinary generator tamper rejection: passed
recomputed generator manifest rejection: passed
unmanifested nested same-name rejection: passed
current evaluation anchor mismatch rejection: passed
legacy valid anchor acceptance: passed
legacy invalid anchor rejection: passed
current and legacy anchors both present rejection: passed
neither current nor legacy anchor present rejection: passed

12 passed
```

### Independent Contract Matrix

R4 constructed each contract directly through the production freezer:

```text
current valid: accepted
legacy valid: accepted
candidate tamper plus recomputed generator manifest: rejected
  evaluation artifact root authority mismatch
nested first_pass/artifact_hash_manifest.json: rejected
  generator artifact set does not match hash manifest
legacy invalid root: rejected
current and legacy anchors both present: rejected
neither current nor legacy anchor present: rejected
```

### Finding Closure

#### Closed P1-1: nested same-basename file bypass

Both generator and freezer now exclude only the exact root-relative `artifact_hash_manifest.json`. A nested file with the same basename is a normal artifact: the generator includes it, and the freezer rejects it if it is absent from the descriptor set. The independent bypass probe that previously succeeded now fails closed.

#### Closed P1-2: current contract lacks an independent root authority

`_benchmark_execution_manifest()` reads and validates the generator root SHA-256 and persists it in `quality_evaluation_manifest.execution.generator_artifact_root_sha256`. The freezer compares its independently recomputed generator root against that evaluation authority. Modifying a candidate and recomputing the generator-side manifest now fails with `evaluation artifact root authority mismatch`.

The authority remains outside the generator evidence tree, avoiding the circular digest problem identified in the prior review.

#### Closed P1-3: legacy root anchor is unusable

Current and legacy contracts are explicit and mutually exclusive. Current evidence hashes every generator file except the root control manifest and uses the evaluation authority. Legacy evidence hashes only `first_pass` and `final_after_auto_repair` and validates `generation_manifest.artifact_root_sha256`. Valid legacy evidence freezes successfully; an invalid root, both contracts, or neither contract fails closed.

### Path And Privacy Regression Check

- Fresh single and multi generation keep immutable roots separate from evaluation output.
- Public task-run projection occurs only for explicit `--run-artifacts` replay, including multi-case replay.
- `repair_summary.json` remains the strict three-field evaluator contract.
- Repair provenance remains in the hashed `workbench_audit.json` rather than crossing the evaluator/public truth-redaction boundary.
- No hidden-truth field or mutable public projection was introduced into fresh generator evidence.

### Contract Matrix Coverage

The supplemental parameterized regression now commits both remaining mutual-exclusion states through production `_freeze()`:

```text
test_freezer_rejects_ambiguous_generator_anchor_contract[both]: passed
test_freezer_rejects_ambiguous_generator_anchor_contract[neither]: passed

2 passed in 1.48s
```

Current valid, current invalid/recomputed, legacy valid, legacy invalid, both, and neither are now all represented in the committed freezer suite. No residual test gap remains for the reviewed anchor matrix.

R4 no longer blocks this formal-baseline diff. `baseline_status: pending` remains a separate release gate until the formal baseline itself is completed and frozen.
