---
feature_ids: [F014]
topics: [review-request, json-schema, agent-runtime-contract, task-2]
doc_kind: review-request
created: 2026-08-04
---

# F014 Task 2 Independent Contract Review Request

Review-Target-ID: `f014`

Branch: `codex/skill-first-agent-runtime`

Required reviewer profile: `gpt-5.6-sol`, reasoning `high`.

## Original Requirements

Source: `/Users/shepard/Downloads/codetalk_skill_first_refactor_plan.md`

> 正式实现前必须先 Review 并固定六份 V1 契约。不得在 UI 和数据库先行实现后再倒推 Schema。Producer 与 Judge 使用隔离 Session。AI 只生成 Review 和 Patch；不得静默修改或自动发布。

Also review against `docs/features/F014-skill-first-runtime.md`, especially
AC-A1, AC-A4, AC-C2, AC-C4, AC-D3, AC-D4, and AC-D6.

## Handoff

**What:** Review the three accepted ADRs, Agent Runtime Contract, six JSON
Schemas, contract fixtures/tests, model-provenance fields, and digest boundary.
This is the mandatory contract gate before the safe importer begins.
This is a re-review after the first verdict requested three P1 fixes and one P2
fix; inspect the remediation rather than relying on the author's summary.

**Why:** Importer/compiler/store/runtime code must consume a stable terminal
contract. A permissive or internally inconsistent Schema would push ambiguity
into every later phase.

**Tradeoff:** Schema handles structural and locally expressible invariants.
Cross-document references, duplicate semantic IDs, DAG/cycle checks,
producer/consumer integrity, Session-ID inequality, and normalized Unicode path
collisions remain explicit validator/compiler/runtime checks; they are not
approximated with misleading JSON Schema rules.

**Open Questions:** Confirm that the closed capability vocabulary matches the
Runtime Contract; Producer and Judge have complete independent runtime and
preflight envelopes; failed preflight cannot coexist with a Session; required
Judge locality and artifact scope fail closed while an optional Judge may still
run; Review findings retain timestamp, file/field location, reason, impact, and
recommendation without arbitrary payloads; and Review evidence cannot alter
deterministic content identity. Look for contradictions between ADRs, Runtime
Contract, Schemas, fixtures, and the original refactor plan.

**Next Action:** Return findings first, ordered P0/P1/P2/P3 with exact file and
line references. End with an explicit `APPROVE` or `CHANGES_REQUESTED`. Do not
edit files or commit. Task 3 is blocked until `APPROVE`.

## Review Inputs

- `docs/decisions/adr-027-skill-first-product-model.md`
- `docs/decisions/adr-028-skill-build-release-review.md`
- `docs/decisions/adr-029-skill-runtime-boundary.md`
- `docs/contracts/AGENT_RUNTIME_CONTRACT.md`
- `backend/app/schemas/skills/*.schema.json`
- `backend/tests/fixtures/skills/contracts/**`
- `backend/tests/test_skill_schemas.py`
- `docs/review-notes/f014-task2-contract-quality-gate.md`

## Verification

```bash
cd /Volumes/Media/codetalk-skill-first-agent-runtime/backend
PYTHONPATH=. uv run --python 3.12 --with-requirements requirements.txt \
  pytest -q tests/test_skill_schemas.py tests/test_skill_source_inventory.py
```

Expected current result: `166 passed, 1 skipped`.

The quality-gate report documents three confirmed `main`-identical OpenCode
argument-test failures outside the Task 2 change set. Independently verify that
classification if it affects the verdict.

## First-Review Findings And Red-Green Evidence

1. Complete Producer/Judge runtime envelopes, requested capabilities, five
   timeout classes, and preflight-to-Session gating: adversarial RED to full
   contract GREEN.
2. Required Judge non-empty checked-artifact scope: single-mutation RED to
   GREEN.
3. Timestamped actionable Review findings with file/field locations, reason,
   impact, and recommendation: single-mutation RED to GREEN.
4. Non-empty required Skill/IR execution groups: twelve RED mutations to GREEN.
5. Follow-up optional-Judge test: `sessions.judge` RED under the over-restrictive
   intermediate rule, then GREEN while null/failed Judge preflight remains
   fail closed.
6. First re-review P1: optional-executed Judge accepted false isolation or an
   empty artifact scope. Two single-variable tests were RED (`2 failed, 163
   deselected`); execution-conditioned constraints are now GREEN, while the
   optional-not-run state remains valid. The full suite is `166 passed, 1
   skipped`.
