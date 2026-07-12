---
feature_ids:
  - release-debt-zero
  - F002
topics:
  - artifact-delivery
  - staged-llm
  - agent-sandbox
  - gitnexus-capacity
  - workbench-maintainability
doc_kind: implementation-plan
created: 2026-07-12
status: completed
completed: 2026-07-12
---

# CodeTalk Release Debt Zero Implementation Plan

**Feature:** Release debt zero and F002 closure
**Goal:** Close every accepted release-candidate product and truth-source debt except Windows real-machine regression.
**Acceptance Criteria:** Multi-file delivery; automatic staged builtin execution; consistent executor recovery; macOS/Linux sandbox boundary; Clowder same-task evidence; visible GitNexus queue; split Workbench implementation; current Feature/docs truth; full gates and real-browser evidence.
**Architecture:** Extend the existing task artifact ledger instead of creating a second artifact system. AI threads gain a run-scoped artifact directory and manifest, builtin long tasks compile to a staged execution plan, external commands receive an explicit sandbox policy, and GitNexus exposes queue snapshots from its existing serialization lock. Workbench routes keep shared state in a controller hook while each page view moves to a dedicated component.
**Tech Stack:** FastAPI, aiosqlite, Python asyncio/subprocess, Next.js/React/TypeScript, Playwright.
**Frontend Validation:** Yes - real browser hover, click, typing, download and bounded-layout checks are mandatory.
**Not Building:** Windows real-machine validation; a replacement LLM; hidden acceptance of low-quality model output; a second workflow engine.

## Task 1: Multi-file AI Thread Delivery

**Files:**
- Modify: `backend/app/services/ai_conversations.py`
- Modify: `backend/app/api/ai_conversations.py`
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/app/ai/[id]/page.tsx`
- Test: `backend/tests/test_ai_conversations.py`
- Test: `frontend/e2e/agent-workbench.spec.ts`

1. Add red tests for a run producing multiple declared Markdown/JSON/Python files.
2. Require `artifact_manifest.json` entries with path, media type, audience, producer, schema status, size and sha256.
3. Reject unsafe paths, empty required files, invalid JSON/schema and undeclared files.
4. Add manifest, individual-file and deliverables ZIP download endpoints.
5. Render a compact deliverables list in chat; keep diagnostics collapsed.
6. Verify browser downloads and sha256 equality.

## Task 2: Automatic Staged Builtin Execution

**Files:**
- Create: `backend/app/services/ai_staged_execution.py`
- Modify: `backend/app/services/ai_conversations.py`
- Modify: `backend/app/services/test_activity_contract.py`
- Test: `backend/tests/test_ai_staged_execution.py`
- Test: `backend/tests/test_ai_conversations.py`

1. Add red tests proving a comprehensive task currently performs one oversized call.
2. Compile eligible contracts into ordered stages: source analysis, flow, SFMEA, black-box cases, final summary.
3. Give each stage bounded inputs, declared outputs and prior accepted artifact references.
4. Persist `staged_execution_plan.json` and per-stage lifecycle/result artifacts.
5. Retry only truncated/failed stages while retaining the exact original user request.
6. Aggregate accepted stage artifacts into the run manifest and final compact answer.

## Task 3: Executor Safety And Recovery

**Files:**
- Create: `backend/app/services/agent_sandbox.py`
- Modify: `backend/app/services/agent_cli_bridge.py`
- Modify: `backend/app/services/external_agent_discovery.py`
- Modify: `backend/app/config.py`
- Test: `backend/tests/test_agent_sandbox.py`
- Test: `backend/tests/test_agent_cli_bridge.py`
- Test: `backend/tests/test_external_agent_discovery.py`

1. Add red tests for sandbox policy resolution, environment filtering and unavailable-policy failure.
2. Implement audited macOS `sandbox-exec` and Linux bubblewrap policy adapters where available.
3. Restrict writes to run artifact directories, preserve read access to the selected workspace, filter secrets, and make network/subprocess policy explicit.
4. Fail closed when a workflow requires isolation but no supported sandbox exists; otherwise show a Chinese degraded-mode warning.
5. Keep activity-aware timeout, resume identity, UTF-8 buffering and quality retry behavior covered across providers.

## Task 4: GitNexus Capacity Control

**Files:**
- Modify: `backend/app/adapters/gitnexus.py`
- Modify: `backend/app/api/system.py`
- Modify: `frontend/src/app/workspaces/page.tsx`
- Test: `backend/tests/test_gitnexus_adapter.py`
- Test: `frontend/e2e/workspace-list-performance.spec.ts`

1. Add red tests for bounded FIFO queue state and repeated 429 with Retry-After.
2. Track queued, running, retrying, cooldown and capacity timestamps per GitNexus endpoint.
3. Apply bounded exponential backoff with jitter and server Retry-After support.
4. Expose capacity state through health/status payloads and Chinese UI guidance.
5. Run consecutive real workspace indexing and confirm eventual success without duplicate jobs.

## Task 5: Workbench Component Decomposition

**Files:**
- Create: `frontend/src/app/workbench/components/*`
- Create: `frontend/src/app/workbench/hooks/use-workbench-controller.ts`
- Create: `frontend/src/app/workbench/lib/*`
- Modify: `frontend/src/app/workbench/agent-workbench-experience.tsx`
- Test: `frontend/e2e/agent-workbench.spec.ts`
- Test: `frontend/e2e/workbench-real.spec.ts`

1. Freeze route and accessibility behavior with existing E2E and component contracts.
2. Extract pure workflow/audit/format helpers first.
3. Extract Run Cockpit, Designer and Semantic Library view components.
4. Move state/effects/actions into a shared controller hook with typed view models.
5. Keep route wrappers thin and reduce the original file below 4,000 lines.
6. Re-run screenshot and interaction regression at desktop/mobile sizes.

## Task 6: Clowder Comparison And Truth-Source Closure

**Files:**
- Modify: `docs/features/F001-graphsearch-ui.md`
- Modify: `docs/features/F002-clowder-agent-parity.md`
- Modify: `BACKLOG.md`
- Modify: `PLAN_V2.md`
- Modify: `docs/DEPLOYMENT.md`
- Modify: `docs/TEST_ACTIVITY_PRODUCTIZATION.md`
- Modify: `docs/AGENT_WORKBENCH_ROADMAP.md`
- Modify: `docs/plans/2026-07-11-release-candidate-productization.md`
- Add/update: release validation reports outside the repository.

1. Start local Clowder without Docker and provision a runnable member.
2. Run the same SPDK iSCSI Login task through both products using real browser interaction.
3. Compare lifecycle, process collapse, continuity, source evidence and artifacts.
4. Close F002 only when evidence matches; otherwise fix and rerun.
5. Mark F001 superseded/archived because its old always-dark GraphSearch direction conflicts with the current product.
6. Replace stale Pending/pre-release/waiting text with final truth and archive historical DeepWiki plans explicitly.

## Task 7: Release Gates

1. Run focused red-green suites after every task and commit coherent changes.
2. Run backend full regression, frontend E2E, lint, TypeScript, production build and deployer tests.
3. Run secret scan, artifact hygiene and Windows contract tests without claiming Windows real-machine coverage.
4. Request independent review and resolve every P0/P1/P2 finding.
5. Deploy the candidate on isolated ports and run real-browser end-to-end validation.
6. Fast-forward `feat`, push `origin/feat`, remove the temporary worktree/branch, restart public `3003/3004`, and run final browser smoke.
