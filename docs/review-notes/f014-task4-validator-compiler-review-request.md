---
feature_ids: [F014]
topics: [review-request, skill-validator, skill-ir-compiler, task-4]
doc_kind: review-request
created: 2026-08-05
---

# F014 Task 4 Validator And IR Compiler Review Request

Review-Target-ID: `f014-task4`

Branch: `codex/skill-first-agent-runtime`

Required reviewer profile: `gpt-5.6-sol`, reasoning `high`.

## Original Requirements

Source: `docs/features/F014-skill-first-runtime.md` AC-A4/AC-A5 and
`docs/plans/2026-08-04-f014-skill-first-runtime.md` Task 4.

> Validate references, IDs, dependencies, producers/consumers, outputs,
> scripts, Judge contract, and file paths. Compile only validated input. Golden
> tests bind every IR field to a source file or explicit deterministic default.

## Handoff

**What:** Review Task 4's validator/compiler modules and tests as the
deterministic boundary between imported source packages and terminal Skill IR.

**Why:** Bad validation would let broken references, cycles, missing files,
undeclared producers, invalid paths, or schema-invalid IR flow into immutable
build and runtime work.

**Tradeoff:** The compiler currently implements deterministic V1 behavior only.
It does not create Skill store records, release ZIPs, review records, runtime
invocations, or frontend behavior; those remain later tasks.

**Open Questions:** Look for false positives/negatives in reference validation,
diagnostic path instability, topological-order mistakes, content digest
non-determinism, schema-invalid generated IR, Codetalks v2.4 semantic loss, and
compiler behavior that infers source semantics from prose rather than the
manifest. This request includes remediation for two prior independent
`CHANGES_REQUESTED` verdicts: v2.4 now goes through manifest-shape,
validator, and final IR schema gates; generic compile rejects
document/source mismatch; artifacts are single-producer; delivery artifacts
must be delivery-visible and consumed; `run_guard` logs point to
`内部索引/运行状态.json`; unsafe and symlinked source paths are rejected; selected
scenario workflows are bound into terminal IR input labels, the first step
`instruction_path`, and step titles; artifact output path ambiguity is rejected;
manifest guard fields and Step 04 flow gates are retained in `completion_gate`;
`issue-regression` declares its MR link input; invalid JSON returns stable
validation errors; and the gate evidence has been refreshed.
The latest patch also restores Step 01 to its own instruction file, binds the
selected workflow at the IR root, makes Judge scenario-dependent, and normalizes
invalid UTF-8 into deterministic validation errors.
It also enforces module-analysis required-artifact cardinality and the exact
eight formal-output set before producing IR.
Latest patch adds strict validation for `selected_workflow_path` and
glob-aware strict validation for `completion_gate.requires_glob`.
Latest patch also enforces module-analysis exact step IDs, core-rule IDs, and
the full required-artifact path set.
Latest patch additionally enforces the exact per-step required-artifact sets so
Step 08 Judge-state outputs and Step 09 formal outputs cannot be swapped while
the global 37-path set remains unchanged.

**Next Action:** Return findings first with P0/P1/P2/P3 and exact file/line
references. End with `APPROVE` or `CHANGES_REQUESTED`. Do not edit files or
commit. Task 5 remains blocked until the verdict is `APPROVE`.

## Review Inputs

- `backend/app/services/skill_package_validator.py`
- `backend/app/services/skill_ir_compiler.py`
- `backend/app/schemas/skills/codetalk-skill-v1.schema.json`
- `backend/tests/test_skill_package_validator.py`
- `backend/tests/test_skill_ir_compiler.py`
- `backend/tests/fixtures/skills/contracts/positive/codetalk-skill-v1.json`
- `backend/tests/fixtures/skills/contracts/positive/skill-ir-v1.json`
- `backend/tests/fixtures/skills/codetalks-v2.4/expected-ir-summary.json`
- `docs/features/F014-skill-first-runtime.md`
- `docs/plans/2026-08-04-f014-skill-first-runtime.md`
- `docs/review-notes/f014-task4-validator-compiler-quality-gate.md`

## Verification

```bash
cd /Volumes/Media/codetalk-skill-first-agent-runtime
CODETALKS_V24_ARCHIVE=/Users/shepard/Downloads/codetalks-fused-v2.4-zh.zip \
  PYTHONPATH=backend uv run --python 3.12 \
  --with-requirements backend/requirements.txt pytest -q \
  backend/tests/test_skill_schemas.py \
  backend/tests/test_skill_source_inventory.py \
  backend/tests/test_skill_package_importer.py \
  backend/tests/test_skill_package_validator.py \
  backend/tests/test_skill_ir_compiler.py
```

Expected: `309 passed`, no skips or warnings.
