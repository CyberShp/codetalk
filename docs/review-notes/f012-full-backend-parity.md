---
feature_ids:
  - F012
topics:
  - regression-testing
  - backend-parity
doc_kind: evidence-note
created: 2026-08-03
reviewed: 2026-08-03
reviewer: Integration Agent
review_effort: high
verdict: ACCEPT_WITH_INHERITED_FAILURES
---

# F012 Full Backend Parity Evidence

## Runs

| Tree | Result | JUnit evidence |
|---|---:|---|
| F012 checkpoint `846c9f6872b1ecc0c9ae87342e0a7f45f8474d42` | 4,593 passed, 79 failed, 8 skipped | `/Volumes/Media/codetalk-quality-evidence/f012-postfix-backend-junit.xml` |
| Detached pre-F012 base | 4,090 passed, 73 failed, 8 skipped | `/Volumes/Media/codetalk-quality-evidence/f012-base-backend-junit.xml` |

The F012 tree collected 509 additional tests. Most shared failures occur in both trees and concern pre-existing OpenCode `--auto` argument expectations, Phase 6/7 authority behavior, V3 governance/runtime expectations, and settings/network-policy assertions.

## Active-only Failure Audit

JUnit set comparison found ten active-only failures, all in `tests/test_agent_workbench_api.py`. Each of those exact tests was rerun as one isolated selection on both trees:

```text
F012 tree: 10 passed, 151 deselected
base tree: 10 passed, 150 deselected
```

The isolated parity proves those ten outcomes are full-suite order/shared-state instability rather than a stable F012 behavior regression. Four base-only failures likewise disappeared from the F012 full run. The repository-wide suite is not globally green on either revision; F012-specific tests remain the authoritative incremental gate and passed after the latest integration correction (`513 passed`).

## Disposition

No deterministic F012 regression is confirmed by the full-suite comparison. The inherited full-suite debt remains explicit and must not be reported as a clean repository-wide pass. A final F012-tree full run is still required after the last implementation commit.
