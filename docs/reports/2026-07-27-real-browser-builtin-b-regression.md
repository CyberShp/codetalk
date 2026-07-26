---
feature_ids:
  - workflow-productization-v3
topics:
  - browser-e2e
  - quality-gate
  - staged-execution
doc_kind: regression-report
created: 2026-07-27
---

# Builtin B Real Browser Regression

## Scope

This regression used the published `基础源码 + 设计文档报告（内置模型）`
workflow against `/Volumes/Media/dpdk/spdk`. The Chromium path configured the
two model routes in Settings, created a workspace, uploaded the iSCSI Login
design document, entered the analysis goal, selected the rapid profile,
started the task, opened `report.md`, and downloaded it. No business task was
created through an API call or an intercepted request.

The API process was started in intranet mode with only `api.deepseek.com`
allowlisted for the configured provider route. GitNexus was already available
locally for the SPDK workspace.

## Runs

| Run | Result | What it proved |
| --- | --- | --- |
| `task_run_18db1727e92a4190a16f8d49f314ce17` | completed / passed / complete | First real browser run completed in 468,942 ms and materialized the required Source Evidence Pack, SFMEA, black-box cases, and report. It exposed a report-level SFMEA contradiction that had been recorded only as lint. |
| `task_run_80bcc34b65b64a7e8a83dcf9b5b55b2c` | blocked | After the fact-conflict gate was corrected, a real run stopped at `black_box_cases`: the 500-token format repair attempted to reproduce a 12-row JSON array and was truncated. This was a real execution failure, not a test fixture failure. |
| `task_run_0101c912d2b141448800f791f4c4d430` | completed / passed / complete | Final Chromium E2E completed in 347,813 ms; 48 facts and 24 explicit claims were verified, no blocking quality issue remained, and deliverables were downloadable. The Playwright test passed with trace enabled. |

The final test trace is retained at:

`frontend/test-results/v3-basic-builtin-workflow--65868--with-a-real-built-in-model-chromium/trace.zip`

The final metrics are retained at:

`data/v3-basic-builtin-b-1785102781017-0-0-metrics.json`

## Fixes Validated

1. A combined-report consistency finding such as
   `sfmea_evidence_contradiction` is now a quality-blocking factual issue,
   never a presentation lint warning.
2. A flattened source anchor in `technical_claims` is deterministically
   reconstructed into a schema-valid claim using the exact supplied
   `evidence_id`, file path, and quote. It does not synthesize a new source
   fact or ask a small repair call to regenerate a large array.
3. The real-browser assertion accepts either evidence of an actual source
   provider call or an explicit, SHA-validated evidence reuse event. A cache
   route is visible in metrics rather than impersonating new model work.

## Limits And Follow-up

- The final 347,813 ms run reused verified source evidence and therefore is
  not evidence for the rapid profile's uncached 8-20 minute target. It is
  valid browser/Artifact/quality-chain evidence only.
- The final audit contains three non-blocking rapid-scope coverage warnings.
  They must be surfaced as coverage limitations in the cockpit; they must not
  be described as proof of full iSCSI Login coverage.
- A cold-cache rapid benchmark and the required deep SPDK benchmark remain
  open AC evidence.
