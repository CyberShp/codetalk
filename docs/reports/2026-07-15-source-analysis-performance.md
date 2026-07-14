---
feature_ids:
  - SOURCE_ANALYSIS_PERFORMANCE
topics:
  - source-analysis
  - performance
  - deterministic-evidence
  - benchmark
doc_kind: verification-report
created: 2026-07-15
---

# Source Analysis Performance Verification

## Result

`source_analysis` now materializes a SHA256-validated Source Evidence Pack before an optional model ranking. The model receives only a bounded evidence context, is called at most once, and returns only verified evidence IDs. Deterministic code renders the final paths, lines, symbols, and gaps, so model failure cannot prevent downstream execution or inject unverified source claims. The tested implementation meets all requested timing thresholds with substantial margin.

| Scenario | P50 | P95 / max | Acceptance |
| --- | ---: | ---: | --- |
| Real DeepSeek, uncached, 5 runs | 0.730 s | 1.061 s | P50 <= 5 min; P95 <= 8 min |
| Validated cache hit, 5 runs | 0.0055 s | 0.0078 s | <= 30 s |
| Real-provider forced timeout | 0.0054 s | 0.0054 s | bounded fallback; default total budget is 480 s |

The benchmark is intentionally not a mock: it used the configured `DeepSeek Official` provider and SPDK source files read from disk.

## Baseline Evidence

Legacy runtime artifacts only recorded `attempts`, model, and artifact size. They did not record provider wait, output token usage, finish reason, or elapsed duration, so those historical values are unavailable rather than inferred.

Representative successful legacy artifacts used an 80,108-81,679 character prompt, one provider attempt, and a 5,818-8,647 byte model response. A separate observed failure used two full attempts after provider truncation. That behavior made a second complete source-analysis request possible and is removed for this stage.

The new stage result always records:

- `attempt_count` and `full_retry_performed`;
- prompt characters before/after projection and estimated tokens;
- `provider_wait_ms`, `output_tokens`, and `finish_reason`;
- degradation reason, cache state, quality gate, and configured budgets.

## Test Fixture

- Repository: `/Volumes/Media/dpdk/spdk`
- Commit: `97af299e3c76368219f0cddcc710fafd57edcc1c`
- Target: `分析 SPDK iSCSI login、CHAP 认证、异常恢复和并发会话`
- Search roots: `lib/iscsi`, `test/iscsi_tgt`
- Provider/model: configured DeepSeek Official / `deepseek-v4-flash`
- Evidence per run: 5 source files and 1 test file
- Compact prompt: 7,527 characters / 1,881 estimated tokens
- Provider output budget: 1,600 tokens

## Uncached Runs

| Run | Attempts | Provider wait | Output tokens | Finish | Degraded | Cache | Quality |
| ---: | ---: | ---: | ---: | --- | --- | --- | --- |
| 1 | 1 | 791.5 ms | 46 | stop | no | disabled | passed |
| 2 | 1 | 702.6 ms | 46 | stop | no | disabled | passed |
| 3 | 1 | 716.5 ms | 46 | stop | no | disabled | passed |
| 4 | 1 | 1,051.1 ms | 46 | stop | no | disabled | passed |
| 5 | 1 | 688.1 ms | 46 | stop | no | disabled | passed |

Every run set `full_retry_performed=false` and `repair_attempt_count=0`.

## Cache Runs

The cache was warmed once with a successful, non-degraded, quality-passed result. Five subsequent executions reported `attempt_count=0`, `provider_wait_ms=0`, `finish_reason=cache_hit`, and emitted `stage_reused`.

Wall times were 7.8, 5.5, 5.6, 4.8, and 4.5 ms.

The cache key includes the v3 cache contract, repository commit, analysis target, referenced file SHA256 values, input material SHA256 values, workflow version, and Source Analysis schema version. Restore requires cards and scope to equal the freshly prepared deterministic pack and verifies SHA256 digests for all three cached artifacts. Legacy v2 free-text reports cannot collide with or restore into the v3 deterministic format. A corrupt v3 entry is atomically quarantined and rebuilt; the following execution reuses the repaired entry.

## Timeout And Fallback

A real provider request was started with a deliberately reduced test budget. It completed the degraded stage in 5.4 ms with:

- `attempt_count=1`;
- `finish_reason=provider_timeout`;
- `degraded=true`, reason `provider_timeout`;
- no repair or second full request;
- `source_analysis.md`, `source_scope.json`, and `evidence_cards.json` still present;
- evidence quality gate passed.

Production defaults are 30 seconds for context preparation, 300 seconds for the full provider request, 120 seconds for an optional small repair, and 480 seconds total. A provider timeout or transport error never triggers another full prompt.

## Evidence Integrity

All five uncached runs passed an independent artifact check:

- every referenced path exists at the pinned SPDK commit;
- every full-file SHA256 matches `evidence_cards.json`;
- every line range is within the file;
- every excerpt is present in the declared line range;
- each pack contains source and test evidence;
- all six evidence cards pass the deterministic quality gate.

The model control plane accepts exactly two arrays: `ranked_evidence_ids` and `gap_evidence_ids`. It rejects prose, paths, function calls, unknown IDs, extra fields, duplicates, and over-budget output without a repair call. Only malformed JSON may receive the bounded 500-token repair call. Model raw output is never appended to the report; deterministic code resolves accepted IDs back to validated paths, line ranges, symbols, and classifications. Provider failure cannot remove or rewrite the verified scope/cards.

## Implementation Notes

- `build_source_analysis_context()` projects only target, revision, bounded verified excerpts/symbols, material/tool summaries, gaps, and current-stage constraints. Projection runs under a hard async budget and falls back to the already-verified in-memory evidence projection when the budget expires.
- Source scope and evidence cards are deterministic support stages and are reused without extra model calls.
- Prepare-time source context uses memoization and prefers `git ls-files`; non-Git fallback traversal remains bounded by path hints/search roots.
- Downstream stages execute by dependency-ready level, allowing independent stages to run concurrently.
- Stage-specific model routing and every budget are configurable through `SOURCE_ANALYSIS_*` environment variables.

Raw final benchmark artifacts are stored under `/private/tmp/codetalk-source-analysis-final-v3-benchmark/` on the verification host.

## Regression Gate

- Python compile gate: `python3.11 -m compileall -q backend/app backend/tests` passed.
- Focused source-analysis/workbench/AI-thread/LLM/database/workflow suite: 267 passed. The exact command includes the two named cross-module API/contract tests in addition to the six test modules.
- Full backend suite: 2,329 passed and 8 skipped in 1,238.56 seconds. The final cache self-healing patch was then covered by its red/green tamper-rebuild-reuse tests and the complete 267-test focused suite.
- The real four-piece source-flow API test passes with deterministic scope/card compatibility fields.
- `git diff --check` and root artifact hygiene checks passed.

No frontend files, Redis behavior, or Redis ports were changed.
