---
feature_ids: [F014]
topics: [acceptance, skill-first, evidence, merge-gate]
doc_kind: acceptance-report
created: 2026-08-05
---

# F014 Skill-first Acceptance Report

## Scope

F014 replaces the live product path with `Skill Project -> Skill Version -> Task
-> Run Attempt`. This report records the current final-SHA evidence for the
local product closure gate and the independent review state for Tasks 5-12.

Commit under test: `50e32e9d` plus the current uncommitted F014 branch changes.
Branch: `codex/skill-first-agent-runtime`.

## Evidence Summary

| Gate | Result |
|---|---|
| Official archive contract/build/review/runtime/Judge | `362 passed` with `CODETALKS_V24_ARCHIVE=/Users/shepard/Downloads/codetalks-fused-v2.4-zh.zip` |
| Backend focused Skill-first/legacy gate | `90 passed` |
| Frontend contract scripts | all 17 files passed |
| Frontend lint | pass |
| Frontend TypeScript | pass |
| Frontend production build | pass |
| Skill-first Playwright journey | `1 passed` on Chromium with frontend 3003/API 3004 |
| DeepSeek settings real-provider smoke | `1 passed`; backend observed `POST https://api.deepseek.com/v1/chat/completions` `200 OK` |
| OpenCode real-provider CLI smoke | `opencode 1.18.4`; `deepseek/deepseek-v4-flash` returned `OK` with env file loaded |
| Legacy product source gate | no banned live Workflow/task-draft/prepare-run references |
| Independent legacy/source audit | Feynman (`019fd2ec-9233-76b1-a262-136a213de84a`) `APPROVE` |
| Diff hygiene | `git diff --check` pass |

## Independent Reviews

| Task | Reviewer | Verdict |
|---|---|---|
| Task 5 Store/build | Darwin (`019fd1c0-8375-7a63-a387-8928875fe1b0`) | `APPROVE` |
| Task 6 Review/publish | Singer (`019fd2b5-e77d-7e33-baab-0aca6380e4d7`) | `APPROVE` |
| Task 7 Skill API | Bohr (`019fd2c6-6a99-71f1-9f17-fb957525ef19`) | `APPROVE` |
| Task 8 Task binding | Godel (`019fd2c7-2b72-7c41-bdfb-55454212953e`) | `APPROVE` |
| Task 9 Frozen invocation/runtime bridge | Hegel (`019fd2cd-c8c9-7502-a57a-374c42bd304b`) | `APPROVE` |
| Task 10 Judge/delivery | Aristotle (`019fd2cd-c952-7fa3-a4ad-29a77b8192bf`) | `APPROVE` |
| Task 11 Skill UI slice | Helmholtz (`019fd289-7382-7bb3-9a94-ee8afc1644ed`) | `APPROVE` |
| Task 12 Legacy removal | Kierkegaard (`019fd298-0200-7a12-b55c-e43aba7a3da5`) | `APPROVE` |

Earlier Task 2-4 approvals are recorded in their review response documents.
Task 7-10 quality gates, review requests, and review responses are attached in
`docs/review-notes/`.

## AC Matrix

| AC | Status | Evidence |
|---|---|---|
| AC-A1-A5 | Pass for local and official archive gates | schemas, inventory, importer, validator/compiler, store/build/review: `362 passed` |
| AC-B1-B4 | Pass for deterministic store/review/publish | Task6 `30 passed`, Singer `APPROVE` |
| AC-C1-C3 | Pass for Task binding, frozen invocation, selected delivery filtering | backend focused gate `90 passed`, Playwright Skill-first journey |
| AC-C4-C10 | Pass for local runtime bridge and recovery semantics | Fake Agent lifecycle, adapter failure, frozen invocation, `skill_step` execute hot path, and fail-closed invocation tests pass; OpenCode real-provider CLI smoke passed |
| AC-C11 | Pass for Skill-first live path; legacy helpers retained | Skill-first V3 prepare skips old staged plan setup; reusable legacy validation helpers remain for compatibility |
| AC-D1-D5 | Pass for deterministic local fixture and official archive contract gates | official ZIP `362 passed`, Judge/delivery focused tests in `90 passed` |
| AC-D6 | Pass for local provider readiness smoke | DeepSeek settings UI positive test passed against the real API; OpenCode CLI returned `OK` on `deepseek/deepseek-v4-flash` |
| AC-E1-E3 | Pass | no Workflow selection in Task wizard, no live `/workflows` routes, no task-draft/prepare-run client path |
| AC-E4 | Pass for focused local product gates | full backend suite still contains stale deleted-Workflow tests and is not used as a green claim |
| AC-E5 | Pass for local Skill-first cockpit path | Playwright journey validates Task creation, execute, frozen invocation, and run cockpit path |
| AC-E6 | Pass for reviewed scopes | Task5-12 have explicit independent `APPROVE`; no self-review used |

## Formal Product Closure

The branch has reached local Skill-first product closure for Tasks 5-12:
implementation, focused backend gates, official archive/runtime/Judge gates,
frontend scripts/lint/typecheck/build, Skill-first execute Playwright, real
provider smoke, legacy/source audit, and independent review approvals are
complete.

It is ready to enter formal intranet qualification on public local defaults:
frontend `3003`, API `3004`, dev/test Redis `6398` only. Intranet qualification
should capture the longer full OpenCode + DeepSeek V4 Flash CodeTalk vertical,
bounded Clowder comparison, screenshot pack, and real restart/cancel/session-loss
evidence without recording secrets. Those are release-qualification artifacts,
not open implementation tasks for this F014 branch closure.

## Test Debt Not Used As A Green Claim

`tests/test_v3_workflow_runner.py` as a full suite currently contains stale
handwritten V3 snapshot fixtures that fail before node execution on
`agent_execution_descriptors.json` hash validation. The production snapshot
guard is intentionally strict; the F014 `skill_step` hot path is covered by a
focused passing regression and the broader focused gates above.
