---
feature_ids:
  - SOURCE_ANALYSIS_PERFORMANCE
topics:
  - source-analysis
  - performance
  - deterministic-evidence
  - staged-execution
doc_kind: implementation-plan
created: 2026-07-15
---

# Source Analysis Performance Implementation Plan

**Feature:** `SOURCE_ANALYSIS_PERFORMANCE`
**Goal:** Make `source_analysis` complete in about five minutes normally and always degrade to verified deterministic evidence within eight minutes.
**Acceptance Criteria:** Preserve SHA256-validated file, symbol, line, and test evidence; uncached P50 <= 5 minutes and P95 <= 8 minutes; cache hit <= 30 seconds; provider timeout degrades within 8 minutes; record attempt, prompt, wait, output, finish, retry, degradation, cache, and quality metrics.
**Architecture:** Build and persist a deterministic Source Evidence Pack before any model call. Give an optional stage-specific model only a bounded source-analysis context, use at most one full provider request, and treat model output as an enhancement rather than the source of truth. Execute downstream ready stages by dependency level and reuse deterministic support artifacts.
**Tech Stack:** Python 3.11, asyncio, Pydantic settings, pytest, Git CLI, JSON/Markdown artifacts.
**Frontend Validation:** No new controls are required; Workbench events and existing Run Cockpit consume the new stage metrics and reuse/degradation events.

---

## Finish line

The final system materializes `source_analysis.md`, `source_scope.json`, and `evidence_cards.json` from verified local evidence before calling an LLM. The optional LLM ranks evidence, summarizes supported facts, and marks gaps from a bounded prompt; it cannot rediscover files or block the workflow. This change does not reduce evidence verification or weaken later SFMEA and black-box quality gates.

## Terminal contracts

- Source Evidence Pack schema version: `source-evidence-pack-v1`.
- Source Analysis cache schema version: `source-analysis-cache-v2`.
- Stage metrics include `attempt_count`, prompt sizes before/after compaction, provider wait, output tokens, finish reason, repair/full retry flags, cache/degradation state, and elapsed budgets.
- Configurable limits: stage model config, max tokens, timeout, total timeout, max files, excerpt characters, evidence anchors, context timeout, repair tokens, and repair timeout.

## Tasks

### 1. Freeze red tests

**Files:**
- Modify: `backend/tests/test_ai_staged_execution.py`
- Modify: `backend/tests/test_workbench_task_run.py`

Test deterministic materialization, compact prompt bounds, one-call timeout fallback, metrics, cache reuse, prepare memoization, Git file discovery, and dependency-level parallelism. Run each test before implementation and retain the expected failure evidence.

### 2. Build deterministic evidence and compact context

**Files:**
- Modify: `backend/app/services/ai_staged_execution.py`
- Modify: `backend/app/services/workbench_task_run.py`

Add Source Evidence Pack rendering, context compaction, SHA/revision/cache key calculation, cache validation, and direct support-artifact materialization.

### 3. Enforce provider and time budgets

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/llm/base.py`
- Modify: `backend/app/llm/openai_compat.py`
- Modify: `backend/app/llm/anthropic.py`
- Modify: `backend/app/llm/factory.py`
- Modify: `backend/app/services/ai_staged_execution.py`
- Modify: `backend/app/services/workbench_workflow_runner.py`
- Modify: `backend/app/services/ai_conversations.py`

Route an optional fast model configuration to `source_analysis`, issue at most one complete provider request, cancel it on timeout or stage-budget exhaustion, and continue with deterministic artifacts. Record actual provider usage and finish reason.

### 4. Remove repeated discovery and serial scheduling

**Files:**
- Modify: `backend/app/services/workbench_task_run.py`
- Modify: `backend/app/services/ai_staged_execution.py`

Memoize identical prepare-time source queries, prefer `git ls-files`, constrain fallback traversal with path hints/search roots, and schedule independent ready stages concurrently.

### 5. Verify and benchmark

**Files:**
- Add: `docs/reports/2026-07-15-source-analysis-performance.md`

Run focused and related backend regressions, then execute the same real repository/commit/target five times. Report P50/P95, per-run stage metrics, cache status, degradation, and artifact quality. Complete independent review before pushing `feat`.
