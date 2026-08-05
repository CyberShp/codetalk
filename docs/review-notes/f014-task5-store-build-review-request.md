---
feature_ids: [F014]
topics: [review-request, skill-store, skill-build, task-5]
doc_kind: review-request
created: 2026-08-05
---

# F014 Task 5 Store And Build Review Request

Review-Target-ID: `f014-task5`

Branch: `codex/skill-first-agent-runtime`

Required reviewer profile: independent reviewer; no self-review.

## Original Requirements

Source: `docs/features/F014-skill-first-runtime.md` AC-B1 and
`docs/plans/2026-08-04-f014-skill-first-runtime.md` Task 5.

> Store mutable Draft content in filesystem directories and metadata in the
> existing Workbench SQLite database. Produce a staged ZIP, unpacked copy, IR,
> validation report, file digest map, and deterministic content digest
> atomically, but do not publish a Skill Version before the required Review
> decision. Do not add an object-store class.

## Handoff

**What:** Review Task 5's Skill store and deterministic build candidate
pipeline.

**Why:** Task 6 review/publish and later Task binding must consume a Draft/Build
boundary where filesystem source is authoritative and pre-review build
candidates cannot masquerade as immutable published Versions.

**Tradeoff:** This task creates metadata tables and candidate artifacts only. It
does not implement AI Review, publication, Skill APIs, runtime invocation,
Judge, or UI.

**Open Questions:** Look for database/content-authority drift, non-deterministic
candidate ZIPs or digests, partial build artifacts after failures, accidental
Skill Version publication, unsafe draft source copying, and implementation that
creates an object-store abstraction contrary to Task 5.

**Next Action:** Return findings first with P0/P1/P2/P3 and exact file/line
references. End with `APPROVE` or `CHANGES_REQUESTED`. Do not edit files or
commit. Task 6 remains blocked until the verdict is `APPROVE`.

## Review Inputs

- `backend/app/services/skill_store.py`
- `backend/app/services/skill_build_pipeline.py`
- `backend/tests/test_skill_store.py`
- `backend/tests/test_skill_build_pipeline.py`
- `docs/features/F014-skill-first-runtime.md`
- `docs/plans/2026-08-04-f014-skill-first-runtime.md`
- `docs/review-notes/f014-task5-store-build-quality-gate.md`

## Verification

```bash
cd /Volumes/Media/codetalk-skill-first-agent-runtime
PYTHONPATH=backend uv run --python 3.12 \
  --with-requirements backend/requirements.txt pytest -q \
  backend/tests/test_skill_store.py \
  backend/tests/test_skill_build_pipeline.py
```

Expected: `8 passed`.

```bash
CODETALKS_V24_ARCHIVE=/Users/shepard/Downloads/codetalks-fused-v2.4-zh.zip \
  PYTHONPATH=backend uv run --python 3.12 \
  --with-requirements backend/requirements.txt pytest -q \
  backend/tests/test_skill_schemas.py \
  backend/tests/test_skill_source_inventory.py \
  backend/tests/test_skill_package_importer.py \
  backend/tests/test_skill_package_validator.py \
  backend/tests/test_skill_ir_compiler.py \
  backend/tests/test_skill_store.py \
  backend/tests/test_skill_build_pipeline.py
```

Expected: `317 passed`.
