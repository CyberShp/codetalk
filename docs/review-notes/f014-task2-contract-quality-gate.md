---
feature_ids: [F014]
topics: [quality-gate, json-schema, agent-runtime-contract, task-2]
doc_kind: quality-gate-report
created: 2026-08-04
---

# F014 Task 2 Contract Quality Gate

## Scope

This gate covers Task 2 of the approved phased plan: the six terminal JSON
Schemas, offline positive/adversarial fixtures, and contract tests. It is not a
claim that F014 is complete. Task 3 remains blocked until an independent
`gpt-5.6-sol` high auditor approves this contract slice.

No Workflow, Task, Runner, Harness, Session, Event, Checkpoint, frontend, or
Redis production path changed in this slice.

## Vision And Requirement Check

Sources read in full:

- `/Users/shepard/Downloads/codetalk_skill_first_refactor_plan.md`
- `docs/features/F014-skill-first-runtime.md`
- `docs/plans/2026-08-04-f014-skill-first-runtime.md`
- `docs/decisions/adr-027-skill-first-product-model.md`
- `docs/decisions/adr-028-skill-build-release-review.md`
- `docs/decisions/adr-029-skill-runtime-boundary.md`
- `docs/contracts/AGENT_RUNTIME_CONTRACT.md`

The slice preserves the required product boundary: one Skill per scenario,
Pack as an import container, immutable Version/Attempt identities, Review
evidence outside the deterministic content digest, full execution with
selective delivery, and separate Producer/Judge Sessions over the main durable
runtime stack. F012 and F013 are not implementation inputs.

## Contract Coverage

| Requirement | Evidence | Result |
|---|---|---|
| Six versioned schemas | `backend/app/schemas/skills/*.schema.json` | Pass |
| Offline schema IDs and references | registry validation in `test_skill_schemas.py` | Pass |
| Unknown terminal fields fail closed | `additionalProperties: false` plus adversarial fixtures | Pass |
| Negative fixtures are one declared mutation | fixture-shape and exact-error-path tests | Pass |
| Stable capability vocabulary | shared closed enum used by Skill and IR | Pass |
| Terminal IR retains execution groups | positive IR includes ordered steps, gates, artifacts, deliveries, script boundary, core-rule acknowledgement, Judge, and source digests | Pass |
| Required execution groups cannot be empty | Skill and terminal IR arrays use local non-empty constraints with single-mutation adversarial fixtures | Pass |
| Review evidence is structured | timestamped closed finding and proposed-patch items; findings require file/field locations, reason, impact, and recommendation | Pass |
| Content and Review digests are separate | `content_digest` and `review_evidence_digest` are independent required fields | Pass |
| Invocation freezes runtime evidence | independent Producer/Judge requested/effective provider/model envelopes, requested capabilities, CLI version, capability report ID/digest, preflight receipt, five timeout classes, input/context refs, deliveries, artifact root | Pass |
| Producer/Judge lifecycle gating | failed Producer preflight permits only two null Sessions; null/failed Judge permits no Judge Session; required Judge plus passed preflights requires an isolated Judge Session and non-empty artifact scope | Pass |
| Optional Judge remains executable | optional Judge may use a configured, preflight-passed runtime and its own Session; optional means not required, not prohibited | Pass |
| Clean Session recovery is bounded | invocation requires `max_clean_session_replacements: 1` | Pass |
| DeepSeek acceptance limits are conditional | only requested `deepseek-v4-flash` forces context `200000` and output maximum `4096` | Pass |
| JSON documents contain no duplicate keys | strict parser test | Pass |

Cross-reference integrity, ID uniqueness, DAG cycles, producer/consumer
integrity, and Unicode-normalized path collision rejection deliberately remain
compiler/validator responsibilities in Task 4. Task 2 proves the Schema accepts
the two Unicode spellings so the compiler can diagnose the semantic collision.

## Fresh Verification

Worktree: `/Volumes/Media/codetalk-skill-first-agent-runtime`

Branch/base: `codex/skill-first-agent-runtime`, based on `main@9e1434d9`.

```text
cd backend
PYTHONPATH=. uv run --python 3.12 --with-requirements requirements.txt \
  pytest -q tests/test_skill_schemas.py tests/test_skill_source_inventory.py
=> 166 passed, 1 skipped

PYTHONPATH=. uv run --python 3.12 --with-requirements requirements.txt \
  pytest -q tests/test_agent_runtimes.py tests/test_harness_domain_neutrality.py \
  tests/test_harness_facade.py tests/test_harness_tool_call.py \
  tests/test_workbench_task_run.py --maxfail=3
=> 127 passed, 3 failed
```

The three failures are existing `main@9e1434d9` OpenCode `--auto` argument
expectation failures. The exact three tests fail identically when run directly
on main, and both
`agent_cli_bridge.py` and `test_agent_runtimes.py` have identical SHA-256 values
in main and this worktree. They are therefore recorded as a confirmed baseline
gap for the later OpenCode runtime task, not a Task 2 regression.

## Review Remediation

The first independent review returned three P1 findings and one P2 finding.
All four were reproduced with adversarial tests before implementation:

| Finding | Red evidence | Green evidence |
|---|---|---|
| Incomplete Producer/Judge runtime envelope and invalid preflight/Session coexistence | complete-envelope and role-specific preflight gating mutations failed to be rejected | independent role envelopes and Session gating now pass the full contract suite |
| Required Judge accepted an empty artifact scope | required-Judge empty-artifact mutation was accepted | required Judge now requires at least one checked artifact |
| Review finding lacked actionable audit fields and timestamp | missing timestamp/location/reason/impact/recommendation mutations were accepted | each mutation is rejected at its declared path |
| Required Skill/IR execution groups could be empty | twelve empty-group mutations were accepted | all twelve are rejected at their collection path |

Main-Agent follow-up found that an intermediate rule over-constrained optional
Judge execution. A separate test first reproduced the failure at
`sessions.judge`; the minimal Schema correction then passed that test and the
full `164 passed, 1 skipped` gate.

The first re-review then found one remaining P1: a configured optional Judge
could execute with `isolated_session=false` or an empty checked-artifact scope.
Two tests first proved those invalid variants were accepted (`2 failed, 163
deselected`). The Invocation now conditions isolation and non-empty artifact
scope on an actual non-null Judge Session, preserving the valid optional-not-run
state. The fresh full gate is `166 passed, 1 skipped`. The same re-review's P3
generic `budget` wording was replaced in ADR-027 and AC-C1 with the explicit
output-token ceiling and queue/Agent/script/validation/overall timeout fields.

Additional fresh checks:

```text
git diff --check
=> exit 0

jq empty schemas and all contract fixtures
=> exit 0

strict duplicate-key test
=> pass

scoped credential-pattern scan
=> no matches

root media/design artifact scan
=> no matches
```

No `.pen` design applies and there is no frontend surface in this slice.

## Gate Decision

Task 2 remediation self-check: ready for independent contract re-review. This
report is not review approval and does not authorize Task 3 by itself.
