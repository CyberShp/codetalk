---
feature_ids:
  - AI_THREAD_V2_INTEGRATION
topics:
  - ai-thread
  - workbench-v2
doc_kind: progress
created: 2026-07-14
---

# AI Thread V2 Integration Progress

## Baseline

- Required review baseline: `1b87bb73e2016f542eba0a141d032db6ff21287d`.
- Implementation baseline: `831493ecd90e62291729a9568ced0b0b732edfce` (`origin/feat`).
- Delta reviewed: workflow selection, canvas drag fix, and single-release-preset changes.
- Worktree: `/Volumes/Media/codetalk-ai-thread-v2`.
- Branch: `codex/ai-thread-v2-integration`.
- Main repository user changes are untouched (`.agents/` remains untracked there).

## Phase Status

| Phase | Status | Evidence |
|---|---|---|
| Contract and current-state review | Complete | `docs/AI_THREAD_V2_INTEGRATION_PLAN.md` |
| Snapshot and link migration | In progress | Tests pending |
| AI -> Task Draft | Pending | |
| Run -> AI | Pending | |
| Atomic queue and capacity | Pending | |
| Run Cards, timeline, real actions | Pending | |
| Real E2E and release gate | Pending | |

## Current State Machine

Existing AI queue state is `queued -> running -> completed/failed/cancelled`, but claiming is not atomic. Target state keeps the same public states and adds a transactional queued-to-running claim plus global/provider capacity waiting metadata.

## Compatibility Rules

- Legacy `selected_workflow_id` is readable.
- New bindings also persist `selected_workflow_version_id` and an immutable snapshot.
- Legacy runs display unrecorded snapshot fields; current conversation configuration is never substituted.
- Existing `/workbench` rollback routes remain available for one release but are removed from new V2 primary actions.
- No history, artifact, message, run, workflow version, or task is deleted.

## Verification Log

Commands and exact results will be appended after each Red/Green cycle and the final quality gate.
