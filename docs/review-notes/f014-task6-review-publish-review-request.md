---
feature_ids: [F014]
topics: [review-request, skill-review, skill-publish, task-6]
doc_kind: review-request
created: 2026-08-05
---

# F014 Task 6 Review And Publish Review Request

Review-Target-ID: `f014-task6`

Branch: `codex/skill-first-agent-runtime`

Required reviewer profile: independent reviewer; no self-review.

## Original Requirements

Source: `docs/features/F014-skill-first-runtime.md` AC-B2, AC-B3, AC-B4 and
`docs/plans/2026-08-04-f014-skill-first-runtime.md` Task 6.

> Test seeded semantic contradictions, incremental/full scope, patch proposal,
> explicit apply/reject, and release audit retention. No review operation
> mutates a Draft. Publication is a separate explicit command allowed only
> after the required full Review decision; it atomically creates the immutable
> Version with source, IR, validation, review records, deterministic content
> digest, separate review evidence digest, and manifest linking both.

## Handoff

**What:** Review Task 6's review service, review persistence, patch decisions,
and explicit full-review-gated publication.

**Why:** Later Skill APIs, Task binding, runtime invocation, and Judge flow must
consume immutable Skill Versions rather than mutable Drafts or unreviewed build
candidates.

**Tradeoff:** This task records patch apply/reject decisions but does not apply
patches to Draft files. Actual patch application remains an explicit future
authoring action followed by rebuild and re-review.

**Open Questions:** Look for hidden Draft mutation, review evidence digest drift,
secret/provenance persistence mistakes, publication without full review,
incremental review accidentally satisfying release, non-immutable version
artifacts, DB/file partial-publish states, and acknowledged AI findings being
hidden or treated as structural validation blockers.

**Next Action:** Return findings first with P0/P1/P2/P3 and exact file/line
references. End with `APPROVE` or `CHANGES_REQUESTED`. Do not edit files or
commit. If the verdict is `CHANGES_REQUESTED`, the main task will reproduce,
fix, rerun evidence, and request re-review until `APPROVE`.

## Review Inputs

- `backend/app/services/skill_review.py`
- `backend/app/services/skill_store.py`
- `backend/app/services/skill_build_pipeline.py`
- `backend/tests/test_skill_review.py`
- `backend/tests/test_skill_store.py`
- `backend/tests/test_skill_build_pipeline.py`
- `docs/features/F014-skill-first-runtime.md`
- `docs/plans/2026-08-04-f014-skill-first-runtime.md`
- `docs/review-notes/f014-task6-review-publish-quality-gate.md`
- `docs/review-notes/f014-task6-review-publish-review-response.md`

## Verification

```bash
cd /Volumes/Media/codetalk-skill-first-agent-runtime/backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q \
  tests/test_skill_store.py \
  tests/test_skill_review.py \
  tests/test_skill_build_pipeline.py
```

Expected: `28 passed`.

```bash
cd /Volumes/Media/codetalk-skill-first-agent-runtime/backend
CODETALKS_V24_ARCHIVE=/Users/shepard/Downloads/codetalks-fused-v2.4-zh.zip \
  /Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q \
  tests/test_skill_schemas.py \
  tests/test_skill_source_inventory.py \
  tests/test_skill_package_importer.py \
  tests/test_skill_package_validator.py \
  tests/test_skill_ir_compiler.py \
  tests/test_skill_store.py \
  tests/test_skill_build_pipeline.py \
  tests/test_skill_review.py
```

Expected: `336 passed`.
