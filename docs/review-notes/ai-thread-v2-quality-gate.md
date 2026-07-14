---
feature_ids:
  - AI_THREAD_V2_INTEGRATION
topics:
  - quality-gate
  - ai-thread
  - workbench-v2
doc_kind: review-note
created: 2026-07-15
---

# AI Thread V2 Integration Quality Gate

## Vision and scope

- Authority checked: the supplied `AI Thread V2 Integration` goal, its 25-item Definition of Done,
  `docs/AI_THREAD_V2_INTEGRATION_PLAN.md`, and the implementation baseline `831493ec` on `feat`.
- The product chain has one execution source of truth: AI Thread forms a Task Draft, the Task freezes
  a Published Workflow Version, Workbench compiles and schedules an immutable Attempt, Run Cockpit
  explains the result, and AI can continue from that exact Attempt and its public artifacts.
- AI workflow-constrained answers remain explicitly non-executing. AI Thread does not compile plans,
  impersonate a DAG run, or become a second Workbench runner.
- The main repository's pre-existing untracked `.agents/` directory remains untouched. All changes
  are confined to `/Volumes/Media/codetalk-ai-thread-v2`.

## Definition of Done mapping

| # | Requirement | Result and evidence |
|---|---|---|
| 1 | New AI actions avoid legacy `/workbench` routes | V2 task-draft and exact-run links are used; real E2E asserts the old route is never entered. |
| 2 | AI creates a Task Draft fixed to a Published Version | Server-owned draft API validates ownership/publication and freezes workflow/version; API and browser tests pass. |
| 3 | Wizard restores Workflow, Workspace, and origin | Six-step wizard locks restored facts and resumes at the first missing step; browser flow verifies them. |
| 4 | Real execution creates Task and Task Run IDs | Attempt creation uses the existing task API; browser/API assertions verify both IDs. |
| 5 | Run uses compiled plan and DAG scheduler | The real two-Agent E2E waits for two dependency-ordered nodes and persisted run events. |
| 6 | Single-Agent prompt is not called workflow execution | Snapshot mode distinguishes free, constrained-answer, and task-run discussion semantics. |
| 7 | Run Cockpit creates or reopens an AI thread | Create-or-open bridge and reciprocal browser navigation are covered. |
| 8 | AI shows Task, Attempt, and Workflow Version | Linked header and immutable per-message Run Cards render these facts. |
| 9 | Each assistant message owns its Run Card | Messages load batched run summaries by `message.run_id`, not `latest_run`. |
| 10 | Historical Run uses frozen Runtime snapshot | Runtime-switch and legacy unknown-semantics regressions pass. |
| 11 | Opening a thread does not mutate Runtime | Initialization no longer auto-PATCHes; regression coverage freezes this behavior. |
| 12 | One Conversation cannot run two Agents concurrently | Transactional claim plus true concurrent POST test enforces one running Run. |
| 13 | Duplicate scheduling cannot duplicate spawn | Idempotent kick/claim tests and queue advancement coverage pass. |
| 14 | Agent queue and capacity state are explicit | Global/provider leases expose waiting reasons and positions; coordinator tests pass. |
| 15 | Default UI hides raw IDs/contracts/JSON/local paths | Public summaries and folded redacted diagnostics are used; responsive E2E inspects the UI. |
| 16 | Tool calls and results are paired | Public timeline pairs call IDs and keeps raw payloads in diagnostics. |
| 17 | Memory and suggestion controls are honest | Structured memory action is retained; prompt-only actions are labelled as inserting/asking suggestions. |
| 18 | Desktop/mobile avoid overflow and obstruction | `1440x900` and `390x844` screenshots plus programmatic overflow assertions pass. |
| 19 | Main text/status remains readable | Existing typography contracts and screenshot inspection satisfy the supplied size hierarchy. |
| 20 | New real E2E passes | `ai-thread-v2-integration-real.spec.ts`: real UI chain, two-Agent DAG, no route mocks. |
| 21 | Backend concurrency/snapshot tests pass | Relevant backend suite: `336 passed in 79.52s`; CLI Bridge sandbox suite: `17 passed`. |
| 22 | Lint, TypeScript, build, and regressions pass | ESLint zero warnings, `tsc` exit 0, Next production build exit 0, Chromium group `7 passed`. |
| 23 | Documentation matches behavior | Integration plan, progress log, quality gate, and E2E acceptance chain are current. |
| 24 | Historical data is preserved | Migrations are additive/idempotent; legacy rows return `legacy`/`未记录`; no destructive migration exists. |
| 25 | No second Workflow runner is introduced | AI bridges to Workbench Tasks/Runs and only Workbench compiles/schedules the DAG. |

## Fresh verification evidence

- Backend: `python3.11 -m pytest -q tests/test_ai_thread_v2_integration.py tests/test_ai_conversations.py tests/test_database_init.py tests/test_workbench_task_store.py tests/test_workflow_scheduler.py tests/test_workflow_version_store.py tests/test_agent_workbench_api.py tests/test_workbench_artifact_manifest.py --maxfail=1` -> `336 passed in 79.52s`.
- Agent CLI sandbox: `python3.11 -m pytest -q tests/test_agent_cli_bridge.py --maxfail=1` -> `17 passed`.
- Frontend: `npm run lint -- --max-warnings=0` -> exit 0.
- Frontend: `./node_modules/.bin/tsc --noEmit --pretty false` -> exit 0.
- Frontend: `npm run build` -> exit 0; all Next.js routes compiled and type-checked.
- Chromium: integration plus bounded layout, Task Wizard, Run Cockpit, and Workflow V2 files ->
  `8 passed in 32.1s` against worktree ports `3013/3014`.
- AI source-first Agent regression -> `1 passed in 11.3s` with the default macOS read-only sandbox;
  the spawned wrapper received the real `lib/nvmf/connect.c` body.
- AI quality-retry regression -> `1 passed in 14.0s`; a deliberately incomplete answer failed the
  quality gate, the user retried through the UI, and the complete evidence/flow/SFMEA/eight-dimension
  black-box artifact passed and downloaded. This fixture disables the OS sandbox because its sole
  purpose is cross-Run retry state; sandboxed wrapper execution is covered separately above.
- `git diff --check` -> clean.
- Root artifact scan -> no root media/design artifacts in the working tree or branch diff.

## Browser evidence

- `frontend/output/playwright/ai-thread-v2/run-cockpit-desktop.png` (`1440x900`).
- `frontend/output/playwright/ai-thread-v2/linked-ai-thread-mobile.png` (`390x844`).
- The browser flow uses hover, click, select, typing, and real waiting. API calls only provision a
  deterministic test Workflow/runtime and assert persisted facts; they do not replace the business
  flow or fabricate Task, Run, event, or artifact responses.

## Gate-specific checks

- No matching `.pen` design exists; supplied product requirements and real-browser evidence are the
  visual authority.
- Repository-specific `check-hotfix-pattern.mjs` and `check-fallback-layers.mjs` scripts are absent;
  those checks are not applicable.
- No Redis behavior changed and no test connected to production Redis port `6399`.
- Declared custom deliverables are path-normalized before classification. Run-linked AI artifact
  references enforce task-directory containment, cap public references, and redact Agent context.

## Residual boundary

The unified `AgentRunCoordinator` currently governs AI Conversation Agent subprocesses. Workbench
Agent nodes retain their established runner; migrating both to one cross-runner lease remains an
explicit enhancement and is not claimed by this release. The supplied goal explicitly permits this
boundary when AI spawn is covered and the debt is recorded.

## Independent-review remediation

The first independent review requested changes for six P1 and two P2 findings. All eight now have
focused Red-to-Green coverage: active built-in version enforcement, private frozen runtime execution,
explicit provider persistence, exact Attempt evidence priority, concurrent Run-to-AI idempotency,
full external-Agent prompt redaction, Task Draft source-pair/idempotency validation, and live queue
position updates. The detailed response is in `docs/review-notes/ai-thread-v2-review-response.md`.

During browser verification, a ninth integration defect was found and fixed: macOS sandbox policy
allowed the executable but not a trusted wrapper/config file supplied as an absolute runtime
argument. Existing local argument paths are now admitted read-only, with a real sandbox regression.

The second independent review found five more edge cases. Static configured argv is now separated
from dynamic prompt/session argv before sandbox admission; assistant Run messages are valid Task
Draft sources; source-less and explicit-equivalent draft requests share one per-thread idempotency
boundary; queue callback failures clean their waiter; and operation-lock entries are reference-counted
and evicted. The main AI-to-Task browser acceptance passed again after these fixes.

The final re-review found one adjacent grant/cancel race: a waiter cancelled after capacity was granted
but before its coroutine resumed could leak that slot. Waiters now retain explicit grant ownership so
the cancellation path returns the slot and advances the queue. A deterministic handoff regression is
included in the 336-test backend gate.

## Gate result

Self quality gate: **PASS pending independent re-review**. No Definition of Done item is waived. Push
to `feat` is prohibited until the original independent reviewer reports no unresolved P0/P1/P2.
