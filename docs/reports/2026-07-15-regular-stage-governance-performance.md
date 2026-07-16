---
feature_ids:
  - REGULAR_STAGE_EXECUTION_GOVERNANCE
topics:
  - staged-execution
  - business-flow
  - performance-validation
  - spdk
doc_kind: validation-report
created: 2026-07-15
---

# Regular Stage Governance Performance Validation

## Environment

- Repository: `/Volumes/Media/dpdk/spdk`
- Revision: `97af299e3c76368219f0cddcc710fafd57edcc1c`
- Model: DeepSeek official API, `deepseek-chat`
- Scenario: SPDK iSCSI login, CHAP, digest negotiation, error cleanup, and session recovery
- Driver: Playwright Chromium at 1440x900 using hover, click, select, and real text input
- Evidence directory: `/tmp/codetalk-rsg-e2e-final12`

## Full Workflow Results

| Attempt | Click to terminal | Flow evidence | Business flow | SFMEA | Black box | Business attempts | Quality |
|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 51.619 s | 0.562 s | 10.773 s | 23.701 s | 23.671 s | 1 | needs rework (2) |
| 2 | 14.642 s | 0.003 s reused | 11.653 s | 0.006 s reused | 0.005 s reused | 1 | needs rework (2) |
| 3 | 14.629 s | 0.002 s reused | 10.658 s | 0.009 s reused | 0.003 s reused | 1 | needs rework (2) |
| 4 | 14.635 s | 0.002 s reused | 11.251 s | 0.005 s reused | 0.003 s reused | 1 | needs rework (2) |
| 5 | 14.619 s | 0.002 s reused | 11.750 s | 0.006 s reused | 0.003 s reused | 1 | needs rework (2) |

The complete workflow P50 was **14.635 seconds**, P95 was **44.224 seconds**, and the slowest observation was **51.619 seconds**. All five runs were below the product requirement of 8 minutes and the user-facing 20-minute ceiling. The complete Playwright file, including browser setup and all five runs, finished in 2.1 minutes.

The first uncached run exposed real business-flow content 2.573 seconds after the click. Business-flow stage P50 was 11.251 seconds and its uncached maximum was 11.750 seconds. Source analysis, flow evidence, SFMEA, and black-box support artifacts were reused when their individual quality gates permitted it; rejected business-flow output was not cached. Every generated stage used one full request only. Cache hits recorded `attempt_count=0`.

## Prompt Governance

| Stage | Before compaction | After compaction | Estimated tokens | Reduction |
|---|---:|---:|---:|---:|
| Source analysis | 66,210 chars | 12,238 chars | 3,059 | 81.5% |
| Business flow | 78,314 chars | 28,619 chars | 7,154 | 63.5% |
| SFMEA | 82,582 chars | 30,520 chars | 7,630 | 63.0% |
| Black box | 94,120 chars | 42,805 chars | 10,701 | 54.5% |

No run performed a second full-context request. A syntactically valid SFMEA response with one missing required text field was repaired deterministically and marked as unverified; it did not trigger a provider repair call.

## Acceptance

| Requirement | Result |
|---|---|
| Flow Evidence Pack <= 45 s | Pass, worst observed 0.662 s |
| First user-visible flow <= 60 s | Pass, 2.573 s on the uncached run |
| Flow Outline P50 <= 2 min | Pass, 0.572 s |
| `business_flow.md` P50 <= 3 min | Pass, 11.251 s; uncached max 11.750 s |
| P95 <= 5 min | Pass, full workflow P95 44.224 s |
| Hard cap <= 6 min | Pass; stage policy caps at 240 s by default and 360 s absolutely |
| Cache/retry reuse <= 20 s | Pass, complete cached-support runs <= 14.642 s |
| One full provider attempt | Pass, generated stages `attempt_count=1`; cache hits `0` |
| Evidence authenticity | Pass, file, symbol, line, SHA, revision, and test references validated |

## Quality Signal

The performance acceptance passed. All five final12 attempts were `needs_rework`: `flow_map.md` lacked the required observability section and the declared semantic-import acceptance artifact was absent. The gate continues to expose these as content-quality issues rather than presenting a rejected artifact as a clean delivery.

The workflow also demonstrated selective cache behavior: validated deterministic evidence, SFMEA, and black-box outputs were reused, while the quality-blocked business-flow artifact was regenerated. Performance was not achieved by caching rejected artifacts.

## Engineering Regression Time

The complete backend regression suite is intentionally separate from user workflow latency. It completed with **2,368 passed and 8 skipped in 21 minutes 24 seconds**. The suite includes parser matrices, subprocess timeout/cancellation tests, external-Agent lifecycle tests, and 99 explicit sleep sites; it is a release-confidence cost, not a user-facing execution time. The focused staged-execution and task-run regression completed with **159 passed in 16.44 seconds**, while the broader changed-area regression completed with **276 passed in 22.82 seconds**.
