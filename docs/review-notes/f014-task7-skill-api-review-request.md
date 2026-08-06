---
feature_ids: [F014]
topics: [review-request, skill-api, task-7]
doc_kind: review-request
created: 2026-08-05
---

# F014 Task 7 Skill API Review Request

Review-Target-ID: `f014-task7`

Branch: `codex/skill-first-agent-runtime`

Required reviewer profile: independent reviewer; no self-review.

## Original Requirements

Source: `docs/plans/2026-08-04-f014-skill-first-runtime.md` Task 7.

> Add project/draft/build/review/release/import/read APIs with exact 4xx
> behavior. The main Agent performs the shared `main.py` registration to avoid
> ownership conflicts.

## Handoff

**What:** Review the Skill API router, main registration, and API tests.

**Why:** Task 8+ and the frontend need product-facing Skill operations instead
of direct service calls.

**Tradeoff:** Task 7 exposes API surfaces over existing Task 4-6 services but
does not migrate Workbench Task bindings or frontend callers yet.

**Open Questions:** Look for unsafe import paths, hidden Draft mutation, review
or publish bypasses, wrong 4xx mappings, missing router registration, route
collisions, filesystem-backed record drift, and response payloads that expose
mutable internals as authority.

**Next Action:** Return findings first with P0/P1/P2/P3 and exact file/line
references. End with `APPROVE` or `CHANGES_REQUESTED`. Do not edit files or
commit. If the verdict is `CHANGES_REQUESTED`, the main task will reproduce,
fix, rerun evidence, and request re-review until `APPROVE`.

## Review Inputs

- `backend/app/api/skills.py`
- `backend/app/main.py`
- `backend/tests/test_skills_api.py`
- `backend/app/services/skill_store.py`
- `backend/app/services/skill_build_pipeline.py`
- `backend/app/services/skill_review.py`
- `backend/app/services/skill_package_importer.py`
- `docs/plans/2026-08-04-f014-skill-first-runtime.md`
- `docs/review-notes/f014-task7-skill-api-quality-gate.md`

## Verification

```bash
cd /Volumes/Media/codetalk-skill-first-agent-runtime/backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q tests/test_skills_api.py
```

Expected: `4 passed`.

```bash
cd /Volumes/Media/codetalk-skill-first-agent-runtime/backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q \
  tests/test_skills_api.py \
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
  tests/test_skill_review.py \
  tests/test_skills_api.py
```

Expected: `339 passed` before the additional structured record-drift API case.
