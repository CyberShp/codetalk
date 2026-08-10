---
feature_ids: [F014]
topics: [implementation-plan, skill-runtime, timeout, diagnostics, subagents, tdd]
doc_kind: implementation-plan
created: 2026-08-11
---

# F014 Real Skill Runtime Contract Repair Implementation Plan

**Feature:** F014 — `docs/features/F014-skill-first-runtime.md`
**Goal:** A real-provider Skill Run receives the frozen official method, inputs, profile, and remaining budget; it either produces validated artifacts or leaves a typed, diagnosable failure.
**Acceptance Criteria:** AC-A2-A5, AC-C1-C11, AC-D1-D6, and the timeout/event invariants in `docs/contracts/AGENT_RUNTIME_CONTRACT.md`.
**Architecture:** Keep the existing Task Run, Harness, event, checkpoint, artifact, and Judge path. Replace the generated placeholder preset with the pinned official package, complete the existing Runner-to-Adapter request, and separate the timeout values already declared by the frozen invocation.
**Tech Stack:** Python 3.11, pytest, existing Skill Store/Compiler, Agent Harness, CLI provider adapters.
**前端验证:** No — no frontend structure changes; final verification uses existing Run events and artifacts.

---

## Finish line

A rapid module-analysis run no longer depends on the provider guessing placeholder instructions. Every step can read its frozen official instruction and inputs, uses the remaining Attempt budget instead of receiving a fresh 1800 seconds, and persists enough redacted evidence to distinguish idle, Agent, and overall timeout.

Not included: a new runtime, a second artifact store, dynamic step pruning, database schema changes, frontend redesign, or changes to PANGEA.

## Parallel ownership

### Task A: Official preset source and release upgrade

**Files:**

- Modify: `backend/app/services/skill_presets.py`
- Create: version-controlled official preset resources under `backend/app/resources/skills/`
- Test: `backend/tests/test_skill_presets.py`
- Test when required: `backend/tests/test_skill_ir_compiler.py`

TDD sequence:

1. Add a test proving the current generated step text is rejected as a placeholder.
2. Run the focused test and capture the expected failure.
3. Package the pinned `codetalks-fused-v2.4-zh.zip` source without rewriting its methodology or UTF-8 paths.
4. Seed/publish a new immutable Version only when the official content digest is absent; retain historical Versions.
5. Verify 37/37 source accounting, nine steps, three core rules, eight deliveries, and idempotent restart behavior.

### Task B: Frozen step request and failure projection

**Files:**

- Modify: `backend/app/services/workbench_workflow_runner.py`
- Modify: `backend/app/services/skill_agent_adapter.py`
- Test: `backend/tests/test_skill_runtime_execution.py`

TDD sequence:

1. Add a failing request-capture test for resolved inputs, execution profile, selected workflow, frozen instruction, and prior validated artifacts.
2. Pass those bounded references from Runner to Adapter without copying the source workspace into the prompt.
3. Render one direct prompt contract and retain the existing single Harness execution path.
4. Project an unsuccessful provider result as a typed step failure with session, timeout, and redacted diagnostics instead of reducing it to one error string.

### Task C: Timeout and bounded-read semantics

**Files:**

- Modify: `backend/app/services/skill_run_invocation.py`
- Modify: `backend/app/services/provider_adapters/cli_base.py`
- Modify only if required: `backend/app/services/agent_cli_bridge.py`
- Modify only if the frozen shape changes: `backend/app/schemas/skills/skill-run-invocation-v1.schema.json`
- Test: `backend/tests/test_skill_run_invocation.py`
- Test: `backend/tests/test_cli_provider_adapters.py`
- Test: `backend/tests/test_agent_cli_bridge.py`

TDD sequence:

1. Add failing tests showing Agent, idle, and overall values are currently collapsed.
2. Freeze distinct profile-aware values and preserve the existing five timeout kinds.
3. Make each provider execution consume explicit idle and hard-total values; activity renews only idle time.
4. Bound readable Task Run captures to frozen Skill, frozen input snapshot, and copied inputs; reject arbitrary paths.
5. Retain redacted output/session details in the provider failure result.

## Main integration

The main integrator owns shared caller changes, especially the API call that freezes the selected execution profile and the calculation of remaining Attempt time before each step. It resolves no behavior by adding a fallback or extending 1800 seconds.

## Verification

Run from `backend/` with the shared Python 3.11 environment:

```bash
/Volumes/Media/codetalk-skill-real-execution-fix/backend/.venv311/bin/python -m pytest -q \
  tests/test_skill_presets.py \
  tests/test_skill_ir_compiler.py \
  tests/test_skill_run_invocation.py \
  tests/test_skill_runtime_execution.py \
  tests/test_cli_provider_adapters.py \
  tests/test_agent_cli_bridge.py
```

Expected: all focused tests pass after each RED has been observed for the intended reason.

Then run one bounded local CLI fixture that emits activity, writes the declared first-step artifact, and exits; run a second fixture that stays silent and verify a typed idle timeout with persisted diagnostics. A real intranet OpenCode/DeepSeek vertical remains a deployment qualification gate and must not expose credentials.
