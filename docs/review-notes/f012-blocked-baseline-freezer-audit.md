---
feature_ids: [F012]
topics: [quality-evaluation, baseline-freezer, failure-evidence, runtime-security]
doc_kind: review-note
created: 2026-08-04
---

# F012 Blocked Baseline Freezer Independent Audit

## Scope And Identity

- Reviewed branch: `codex/f012-quality-evaluation-baseline`
- Initial checkpoint: `2da7bac37ed322d44ea442a88b758673f60adec3`
- Accepted fix checkpoint: `91fd1edf9dc1b630a3ed9e710f5faa98a840dafe`
- Reviewers:
  - `agent:019fc9a2-4cad-7943-a79d-e3bfe21e0f14`, R4 runtime/security auditor
  - `agent:019fca40-dfb0-7923-a54c-9ad9027adbd5`, independent peer reviewer
- Independence: neither reviewer authored or modified the reviewed implementation.

The review covered blocked-observation publication, repair-attempt evidence,
producer/freezer terminal contracts, immutable artifact identity, read-only
inputs, secret removal, and the ordinary passed-bundle path.

## Findings And Closure

The initial review rejected the checkpoint for two P1 and three P2 contract
gaps. Red tests reproduced each issue before its fix.

| Finding | Final disposition | Evidence |
|---|---|---|
| Read-only failure trees could not be sanitized in staging | closed | read-only source regression |
| Failed repair attempts were projected without independently recomputable source bytes | closed | raw source, workbench hash, and canonical projection are compared three ways |
| Unknown failure fields could retain secret or truth-shadow data | closed | exact failure field set and secret probes |
| Freezer implementation bytes were not bound into every bundle type | closed | passed and blocked bundles retain `freezer_implementation/` plus `freezer_identity` |
| Producer/freezer terminal taxonomy omitted legal producer codes | closed | parameterized mapping includes postprocess failure, termination failure, and secret-material invalidation |

Additional adversarial probes confirmed that the freezer rejects forged raw
source hashes, orphan repair source bytes, projection arithmetic drift,
status/failure-code mismatches, sensitive keys, and common secret values inside
otherwise allowed fields. Non-quality terminal states strip workbench and
repair attachments before publication and rebuild the staged hash manifest.

Legacy `36a03edc` failure packages predate canonical raw repair traces. They are
accepted only as historical observation evidence with
`repair_attempt_audit_status: unavailable`; that limitation independently
blocks release. The freezer does not infer or fabricate the missing attempts.

## Verification

The accepted delta passed:

```text
focused freezer + generator: 93 passed in 38.82s
complete quality suite:      664 passed in 50.43s
git diff --check:            clean
```

Both reviewers independently returned `ACCEPT` after the fixes. R4 additionally
re-ran the complete quality suite (`664 passed in 49.89s`) and confirmed that
passed and blocked bundles bind the freezer implementation, all legal terminal
codes close, non-quality attachments remain stripped, and raw repair evidence
remains tamper-evident and secret-checked.

## Verdict

**ACCEPT.** No P1 or P2 remains in the blocked-baseline freezer and failure
evidence scope. This code verdict does not turn the formal baseline into a
release pass: complete numeric threshold calibration still depends on 12
evaluable corpus results.
