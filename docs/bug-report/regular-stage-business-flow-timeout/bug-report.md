---
feature_ids:
  - REGULAR_STAGE_EXECUTION_GOVERNANCE
topics:
  - staged-execution
  - business-flow
  - performance
  - recovery
doc_kind: bug-report
created: 2026-07-15
---

# Regular Stage Business Flow Timeout

## Symptom

After an Attempt reused valid `source_scope.json` and `evidence_cards.json`, the builtin model could remain in `business_flow` for more than 30 minutes without a completed artifact or useful progress.

## Root Cause

`source_analysis` had dedicated request governance, but regular stages still used the legacy executor path. That path had no provider deadline, allowed a second full-context attempt, did not force a single provider attempt, waited for the complete response before writing output, and had no stage cache or partial recovery. Reusing source artifacts therefore did not reuse or bound `business_flow`.

The real SPDK browser run also exposed two adjacent defects:

- The task wizard re-fetched a draft after it had already saved and hydrated that same draft. A late response could overwrite text entered on step 3 with the old empty `input_values`.
- Streaming persisted one public event per provider delta. A 6 KB response created 2,231 events and amplified cockpit rendering and storage cost.
- Provider capacity was scoped to each asyncio event loop even though cockpit tasks run in separate worker threads, so concurrent tasks could bypass the intended process limit.
- A valid JSON response with an opening Markdown fence but no closing fence triggered an unnecessary 500-token full-artifact repair, which then truncated the otherwise complete artifact.

## Fix

- Added a shared `StageExecutionPolicy` for all regular stages with one full attempt, bounded provider/total/repair deadlines, optional streaming, degraded output policy, and stage-specific model/token routing.
- Added deterministic, revision-pinned `flow_evidence_pack.json` and `flow_outline.json`. SFMEA now depends on the outline and does not wait for narrative enhancement.
- Added deterministic `business_flow.md` rendering. Model narrative is optional and a timeout preserves the deterministic report plus partial narrative.
- Added compact business-flow context, stage cache, repo/SHA validation, partial continuation, cross-Attempt reuse, quality-retry cache bypass, provider capacity control, and timing metrics.
- Added cockpit events for preparation, evidence counts, first token, output batches, checkpoints, heartbeat, timeout, completion, and reuse. Chain of Thought remains hidden.
- Batched streaming deltas and prevented duplicate task hydration from overwriting current user input.
- Replaced per-event-loop semaphores with process-wide cross-thread Provider capacity and bounded every cancellation wait, including Providers that repeatedly ignore cancellation.
- Applied the same process-wide Provider capacity to `source_analysis`; detached Provider tasks retain their capacity permit until they actually exit.
- Preserved every named input identity, MR link, input summary, MCP, Skill, quality gate, and professional constraint in a globally bounded compact context. Oversized values use a preview, SHA256, size, and stable source reference instead of silent truncation.
- Removed C/C++ comments and string/character literals before extracting flow calls, preventing documentation examples from becoming fake call edges.
- Propagated `partial` through step, workflow, persistence, SSE, and cockpit status instead of reporting it as completed or failed.
- Added deterministic parsing for complete JSON inside an unclosed Markdown fence; small model repair is now reserved for genuinely invalid structure.

## Regression Evidence

- Focused staged/workbench suite: `159 passed in 16.44 seconds`.
- Changed-area staged/workbench/conversation suite: `276 passed in 21.78 seconds`.
- Complete backend suite: `2,368 passed, 8 skipped in 21 minutes 24 seconds`.
- Frontend ESLint: passed.
- Next.js production build: passed.
- Real Playwright run: DeepSeek official API, SPDK commit `97af299e3c76368219f0cddcc710fafd57edcc1c`, five browser-created Attempts, no mock and no API shortcut. Full-workflow P50 was 14.635 seconds, P95 was 44.224 seconds, and the maximum was 51.619 seconds. The complete five-Attempt Playwright file finished in 2.1 minutes.
- Provider timeout, partial preservation, cancellation, small repair, cache invalidation, cross-Attempt reuse, event emission, SFMEA dependency, and provider capacity all have dedicated regression tests.

## Remaining Product Signal

All five final Attempts completed within the performance budget. The quality gate still exposed a missing observability section and a missing declared semantic-import artifact as `needs_rework`; those findings remain visible rather than being hidden as execution success. Performance acceptance therefore does not suppress content-quality signals.
