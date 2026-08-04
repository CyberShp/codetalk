---
feature_ids: [F012]
topics: [quality-evaluation, formal-baseline, vision-gate, release-block]
doc_kind: review-note
created: 2026-08-04
---

# F012 Final Blocked Baseline Audit

## Evidence Identity

- Bundle: `/Volumes/Media/codetalk-quality-evidence/f012-baseline-blocked-c193eb2c`
- Manifest SHA-256: `0e1c49ac9631cfc1530afd81244a0263807445ac2c6950eb0200af24d1daea2d`
- Evaluated CodeTalk revision: `36a03edc2f2d4caf5e78b19766393beaec851a4b`
- Freezer revision: `c193eb2c8b5ec0094cac8c82078104ac6f22c6cc`
- R4 auditor: `agent:019fc9a2-4cad-7943-a79d-e3bfe21e0f14`
- R5 Vision Guardian: `agent:019fc9c3-6f91-7a22-9875-caab929e744b`
- Independence: neither auditor authored or modified the reviewed bundle or code.

## R4 Evidence Audit

**ACCEPT.** R4 found no P1/P2 evidence-integrity defect.

R4 independently verified all 330 manifest artifacts, absence of extra files,
read-only and symlink-safe publication, the exact freezer implementation bytes,
the evaluator Git identity, all generator/evaluation anchors, and the rebuilt
baseline summary and rapid/deep comparison.

The formal corpus has 12/12 attempted coverage: 10 immutable evaluations and
two mutually exclusive terminal failure packages. Mooncake and SPDK both
record a real provider invocation, response/workflow hashes, and structured
work evidence. Their `quality_blocked` status does not mean that the model
produced nothing. They did not produce an evaluable baseline case, and their
legacy evidence predates the canonical repair trace. The bundle therefore
reports `repair_attempt_audit_status: unavailable` without inferring attempt
history.

R4 also recomputed:

| Evidence | Result |
|---|---:|
| Core rapid p100 | `462.808707s` (`pass`) |
| Paired rapid p100 | `397.898292s` (`pass`) |
| Paired deep p100 | `872.87439s` (`pass`) |
| Under-five work sufficiency | `pass` |
| Paired comparison SHA-256 | `2089c5284d6eba12b2cfc49b8280b17bbcf15f73741e8794f8dee23a1756ae07` |

All generator/failure declarations retain `truth_inputs: []`. R4 found no
truth-package name leakage or common private-key, bearer, OpenAI/GitHub, AWS,
JWT, or named credential pattern.

## R5 Vision Disposition

R5 found no unresolved P1 implementation defect and accepted AC1-AC3, AC6,
AC8, the current corpus contract in AC7, and the implemented portions of AC4
and AC5. The implementation preserves the intended architecture: three
non-substitutable axes, hidden-truth isolation, bounded repair before user
retry, all requested domains, and time as a budget rather than a quality score.

The exact final disposition is:

- Implemented scope: **ACCEPT WITH P2 COVERAGE LIMITATIONS**
- F012 release: **BLOCKED**
- Final goal: **blocked**, not complete

The release blockers are:

1. Only 10/12 attempted cases are evaluable, so numeric thresholds and
   complete-corpus axis release gates cannot be frozen.
2. The two real terminal blocks lack canonical repair-attempt evidence, so the
   formal run cannot prove bounded repair exhaustion for those cases.
3. Alternative-model and accepted historical-baseline regression sampling was
   not completed.

Retained P2 expansion limitations are explicit rather than release claims:
the real corpus is Tier S with one critical chain per case; there is no real
multi-chain or E/H sample; harmless-context paraphrase holdout coverage and
positive applicability provenance remain limited. Current evaluation fails
closed and reports L3 limitations instead of inferring hardware behavior.

## Release Conclusion

The bundle proves complete **attempt** coverage and a truthful blocked release.
It does not prove complete evaluable coverage, a passed release, or final
numeric thresholds. `threshold_policy.json` is intentionally absent.
