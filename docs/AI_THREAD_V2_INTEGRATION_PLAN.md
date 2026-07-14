---
feature_ids:
  - AI_THREAD_V2_INTEGRATION
topics:
  - ai-thread
  - workbench-v2
  - workflow
  - task-run
doc_kind: implementation-plan
created: 2026-07-14
---

# AI Thread V2 Integration Implementation Plan

**Feature:** AI Thread V2 Integration
**Goal:** Build one truthful AI Thread -> published Workflow Version -> Task -> immutable Run Attempt -> DAG Scheduler -> Run Cockpit -> AI Thread loop without introducing a second workflow runner.
**Acceptance Criteria:** The 25 Definition of Done items in the task brief, including immutable run snapshots, atomic queue claiming, real browser E2E, readable run cards, and backward-compatible additive migrations.
**Architecture:** AI Thread remains the investigation surface. Workbench V2 remains the only source of truth for Workflow, Task, Run, Scheduler, Events, and Artifacts. Add an additive link table in the primary SQLite database and immutable snapshot columns on AI runs; bridge APIs create or locate Workbench objects but never accept compiled plans from the browser.
**Tech Stack:** FastAPI, aiosqlite/sqlite3, Pydantic, Next.js 16, React, TypeScript, Playwright.
**前端验证:** Yes - real Playwright browser verification at 1440x900 and 390x844.

---

## Finish Line

The user can turn an AI answer into a draft fixed to a published Workflow Version, complete the six-step task wizard, execute a real immutable Attempt through the existing compiled plan and scheduler, inspect nodes/events/artifacts in Run Cockpit, and continue analysis in a thread linked to that exact Attempt.

Not being built: a second AI-side workflow runner, cross-database foreign keys, fabricated snapshots for legacy runs, exposed chain of thought, or browser-authored compiled plans.

## Frozen Object Contracts

- **AI Thread:** investigation, follow-up, decision, retrospective, and task formation.
- **Workflow:** reusable, validated, published execution method.
- **Task:** one concrete job fixed to one published Workflow Version.
- **Run Attempt:** immutable execution snapshot using effective configuration and compiled DAG.
- **Agent Runtime:** execution capacity and spawn configuration.
- **Constraint answer:** an AI answer constrained by a workflow snapshot; creates neither Task nor Attempt and is never labelled workflow execution.

## Current-State Review

### Existing Sources of Truth

- Workflows and immutable versions: `backend/app/services/workflow_version_store.py`.
- Tasks: `backend/app/services/workbench_task_store.py`.
- Effective configuration: `backend/app/services/workbench_task_compile.py`.
- Attempts and frozen task bundles: `backend/app/api/workbench_v2_tasks.py`.
- DAG execution: `backend/app/services/workflow_scheduler.py` and `backend/app/services/workbench_workflow_runner.py`.
- AI conversations/messages/runs/events: primary SQLite tables managed by `backend/app/database.py` and `backend/app/services/ai_conversations.py`.

### Confirmed Gaps

1. AI UI still emits `/workbench` and `/workbench/designer` links; route gates lose workflow/thread context.
2. Selected workflows are converted into a single AI prompt and their Skills/MCP are merged into one runtime invocation.
3. Conversation run scheduling reads latest state before creating/scheduling and marks running without a conditional atomic claim.
4. AI runs do not freeze runtime, workflow binding, Skills, MCP, context, artifact contract, or metrics.
5. Message rendering uses `conversation.latest_run`; historical assistant messages cannot inspect their own run.
6. Page initialization automatically PATCHes an unavailable runtime.
7. Run Cockpit has no Task Run -> AI Thread bridge.
8. Memory/test-design/rerun actions are prompt fillers rather than structured actions.
9. AI status, environment, process, and diagnostics repeat raw implementation details.

## Terminal Data Schema

### Primary SQLite

Add idempotent columns to `ai_conversation_runs`:

- `execution_mode`
- `runtime_type`
- `agent_runtime_id`
- `runtime_snapshot_json`
- `workflow_binding_snapshot_json`
- `skills_snapshot_json`
- `mcp_snapshot_json`
- `context_summary_json`
- `artifact_contract_json`
- `metrics_json`
- `claimed_at`

Add `ai_workbench_links`:

- `id`, `conversation_id`, `message_id`, `ai_run_id`
- `task_id`, `task_run_id`, `relation_type`, `metadata_json`, `created_at`

Relations are textual IDs by design because Workbench tables may live in a separate SQLite file. No historical row is rewritten to imitate a new snapshot.

### Public AI Run View

Every run returns immutable `runtime_snapshot`, `workflow_binding_snapshot`, `skills_snapshot`, `mcp_snapshot`, `context_summary`, `artifact_contract`, `metrics`, queue/capacity status, and a compact public summary. Missing legacy fields return `legacy` / `未记录` semantics.

### Public Process Event

`id`, `time`, `category`, `title`, `summary`, `status`, `duration_ms`, `node_id`, `artifact_ref`, `source_ref`, and `diagnostic_ref`. Tool use/results are paired by call ID. Raw arguments and stdout/stderr remain in folded, redacted diagnostics.

## Implementation Stages

### 1. Data, Snapshot, and Link Foundation

**Files:** `backend/app/database.py`, `backend/app/services/ai_conversations.py`, `backend/tests/test_ai_conversations.py`.

1. Add failing idempotent migration and legacy-read tests.
2. Add run snapshot capture at queue creation and immutable serialization.
3. Add link CRUD/query methods with duplicate-safe indexes.
4. Verify restart reconciliation preserves snapshots and links.

### 2. AI -> Task Draft Bridge

**Files:** `backend/app/api/ai_conversations.py`, `backend/app/services/workbench_task_store.py`, `backend/app/api/workbench_v2_tasks.py`, frontend API/types/task wizard/AI page.

1. Add failing ownership, unpublished-version, fixed-version, origin-link, and missing-input tests.
2. Implement `POST /api/ai/conversations/{conversation_id}/task-drafts` using server-loaded published versions.
3. Persist additive Task origin metadata and AI link.
4. Route the UI to `/tasks/new?task=...&step=...`; fixed workflow/workspace are restored from the Task.
5. Replace new V2 `/workbench` actions with Task Draft or explicit constraint-answer actions.

### 3. Run -> AI Bridge

**Files:** AI API/service, Workbench V2 task API, Run Cockpit, Task Detail, AI page.

1. Add failing create-or-open link and path-redaction tests.
2. Implement Task Run discussion thread lookup/create with public initial context.
3. Show Task, Attempt, Workflow Version, quality/delivery status, and reciprocal links.

### 4. Atomic Queue and Agent Capacity

**Files:** AI service/API, new `agent_run_coordinator.py`, config, agent bridge integration, tests.

1. Add true concurrent POST and duplicate-kick tests.
2. Implement `claim_next_queued_run` in `BEGIN IMMEDIATE` with conditional update.
3. Make `kick_conversation_queue` idempotent and completion/cancel/failure advance the queue once.
4. Add configurable global/provider capacity leases and public queue reasons.
5. Cover AI Agent spawn now; document Workbench-node unification as a non-blocking follow-up only if not safely shareable in this change.

### 5. Run Cards, Timeline, and Real Actions

**Files:** AI API/types/page/CSS, Run Cockpit/CSS, memory/evidence endpoint integration, tests.

1. Return batch run summaries with messages.
2. Render each assistant message against its own immutable run snapshot.
3. Replace duplicate status surfaces with one `本轮运行` summary and folded diagnostics.
4. Pair tool calls/results and explain Run Cockpit nodes/failures/artifact provenance.
5. Implement memory preview/confirm/write; rename any remaining prompt-only action honestly.
6. Preserve detached scrolling and provide visible `跳到最新`.

### 6. Real E2E and Release Gate

1. Add a deterministic local test Agent and real UI-only AI -> Task -> Run -> AI Playwright flow.
2. Run focused backend tests, AI/Workbench regression, migration/concurrency tests, lint, TypeScript, build, existing V2 E2E, and `git diff --check`.
3. Capture desktop/mobile screenshots and overflow checks.
4. Run independent review, resolve P1/P2 findings, commit, and push `HEAD:feat` only after all gates pass.

## Acceptance Matrix

| Area | Proof |
|---|---|
| AI -> Task | API ownership/version tests plus browser-created Task Draft |
| Task -> Run | Attempt contains fixed `workflow_version_id`, `compiled_plan`, `task_id`, `task_run_id` |
| Run -> AI | create-or-open link tests and reciprocal browser navigation |
| Snapshot | runtime-switch regression and legacy unknown semantics |
| Queue | concurrent POST, duplicate kick, completion/cancel/failure continuation |
| Capacity | global/provider limits and queue position/reason tests |
| UI | per-message Run Cards, no automatic runtime PATCH, no old primary links |
| Safety | redaction tests, additive migration, retained history, isolated artifacts |
| E2E | no route mocks and no direct API replacement for the main business flow |

## Risks

- The existing AI service and page are large; extraction must preserve streaming, cancellation, artifact delivery, and source-first behavior.
- Workbench data may be in a separate SQLite file; links must not rely on foreign keys or distributed transactions.
- Process capacity is in-memory per backend process; the shipped coordinator will be explicit about this boundary.
- Existing legacy selected-workflow behavior must remain readable while new actions use published V2 versions.
