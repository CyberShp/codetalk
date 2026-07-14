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

`source_analysis` now materializes a SHA256-validated Source Evidence Pack before an optional model enhancement. The model receives only a bounded evidence context, is called at most once, and cannot prevent downstream execution. The tested implementation meets all requested timing thresholds with substantial margin.

| Scenario | P50 | P95 / max | Acceptance |
| --- | ---: | ---: | --- |
| Real DeepSeek, uncached, 5 runs | 6.296 s | 6.970 s | P50 <= 5 min; P95 <= 8 min |
| Validated cache hit, 5 runs | 0.0048 s | 0.0086 s | <= 30 s |
| Real-provider forced timeout | 0.0044 s | 0.0044 s | bounded fallback; default total budget is 480 s |

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
- Compact prompt: 10,576 characters / 2,644 estimated tokens
- Provider output budget: 1,600 tokens

## Uncached Runs

| Run | Attempts | Provider wait | Output tokens | Finish | Degraded | Cache | Quality |
| ---: | ---: | ---: | ---: | --- | --- | --- | --- |
| 1 | 1 | 5,792.3 ms | 666 | stop | no | disabled | passed |
| 2 | 1 | 6,770.2 ms | 664 | stop | no | disabled | passed |
| 3 | 1 | 6,208.5 ms | 630 | stop | no | disabled | passed |
| 4 | 1 | 6,193.7 ms | 564 | stop | no | disabled | passed |
| 5 | 1 | 6,879.5 ms | 704 | stop | no | disabled | passed |

Every run set `full_retry_performed=false` and `repair_attempt_count=0`.

A final smoke run after the bounded Markdown-repair path was added completed in 5.517 seconds with one full attempt, zero repairs, 495 output tokens, `finish_reason=stop`, and the same passed evidence gate.

## Cache Runs

The cache was warmed once with a successful, non-degraded, quality-passed result. Five subsequent executions reported `attempt_count=0`, `provider_wait_ms=0`, `finish_reason=cache_hit`, and emitted `stage_reused`.

Wall times were 8.6, 5.4, 4.8, 4.4, and 4.3 ms.

The cache key includes repository commit, analysis target, referenced file SHA256 values, input material SHA256 values, workflow version, and Source Analysis schema version. Restore also validates the cached evidence path/SHA pairs against the freshly prepared deterministic pack.

## Timeout And Fallback

A real provider request was started with a deliberately reduced test budget. It was cancelled after 3.1 ms and completed the stage in 4.4 ms with:

- `attempt_count=1`;
- `finish_reason=timeout`;
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

The model enhancement is appended after the deterministic report. Provider failure cannot remove or rewrite the verified scope/cards, and over-budget Markdown is trimmed at a paragraph boundary rather than left malformed.

## Implementation Notes

- `build_source_analysis_context()` projects only target, revision, bounded verified excerpts/symbols, material/tool summaries, gaps, and current-stage constraints.
- Source scope and evidence cards are deterministic support stages and are reused without extra model calls.
- Prepare-time source context uses memoization and prefers `git ls-files`; non-Git fallback traversal remains bounded by path hints/search roots.
- Downstream stages execute by dependency-ready level, allowing independent stages to run concurrently.
- Stage-specific model routing and every budget are configurable through `SOURCE_ANALYSIS_*` environment variables.

Raw benchmark artifacts are stored under `/private/tmp/codetalk-source-analysis-benchmark/` on the verification host.

## Regression Gate

- Python compile gate: `python3.11 -m compileall -q backend/app backend/tests` passed.
- Focused source-analysis/workbench/AI-thread/LLM/database suite: 247 passed.
- Full backend run reached 1,987 passed and 8 skipped before exposing a pre-existing task-card `href` contract mismatch.
- The task-card contract was restored with its declared Workbench URL; that test passes independently.
- The failed file and every remaining test file were rerun as a 470-test segment; all 470 passed.
- The real four-piece source-flow API test passes with deterministic scope/card compatibility fields.
- `git diff --check` and root artifact hygiene checks passed.

No frontend files, Redis behavior, or Redis ports were changed.
