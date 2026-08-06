---
feature_ids: [F014]
topics: [quality-gate, skill-api, task-7]
doc_kind: quality-gate-report
created: 2026-08-05
---

# F014 Task 7 Skill API Quality Gate

## Scope

This gate covers Task 7 only: Skill project, draft, import, build, review,
patch-decision, publish, and read APIs. It does not migrate Workbench Task
bindings, execute runs, implement Judge, build frontend UI, or remove legacy
Workflow product routes.

## Contract Check

- `backend/app/api/skills.py` exposes Skill-first routes under `/api/skills`.
- `backend/app/main.py` registers the Skill router once.
- Project create/read returns stable project metadata and exact 404 details.
- Draft creation from source copies source through `SkillStore` and returns
  filesystem-authoritative draft metadata.
- ZIP import uses the fail-closed `import_skill_package` path and creates one
  Draft per imported workflow source.
- Build API delegates to `SkillBuildPipeline.build_candidate`.
- Review API supports deterministic full/incremental review runs and external
  review evidence recording.
- Publish API delegates to explicit full-review-gated publication and maps
  release precondition failures to 409.
- Version read APIs expose metadata and manifest.
- Exact 4xx behavior is covered for missing project, missing draft, invalid
  project input, invalid archive, publish without review, missing patch, and
  filesystem-backed manifest/review-record/patch-decision drift.

## Verification

```text
cd backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q tests/test_skills_api.py
=> 5 passed

cd backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q \
  tests/test_skills_api.py \
  tests/test_skill_store.py \
  tests/test_skill_review.py \
  tests/test_skill_build_pipeline.py
=> 35 passed

cd backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q \
  tests/test_skill_schemas.py \
  tests/test_skill_package_importer.py \
  tests/test_skill_package_validator.py \
  tests/test_skill_ir_compiler.py \
  tests/test_skill_store.py \
  tests/test_skill_build_pipeline.py \
  tests/test_skill_review.py \
  tests/test_skills_api.py
=> 336 passed, 1 skipped before the publish-lock and patch-decision drift regressions.

cd backend
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
=> 339 passed before adding the structured record-drift API case.

cd backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q \
  tests/test_skill_store.py \
  tests/test_skill_review.py \
  tests/test_skill_build_pipeline.py \
  tests/test_skills_api.py \
  tests/test_workbench_task_store.py
=> 64 passed
```

Additional checks:

- scoped Python compilation for `app/api/skills.py` and `app/main.py`: pass.
- `git diff --check`: pass.

## Review Remediation Notes

Read-only evidence audit raised a candidate P2 that `GET /versions/{id}/manifest`
and `GET /reviews/{id}` could surface corrupted or missing filesystem records as
unstructured 500s. `backend/app/api/skills.py` now maps those record-read
failures to structured `409` responses with `version_manifest_unavailable` and
`review_record_unavailable`; `backend/tests/test_skills_api.py` covers both
paths.

Independent Task 7 review then found the same record-drift class on
`POST /reviews/{review_id}/patches/{patch_id}/decision`, because patch lookup
reads `review.record_path`. That endpoint now maps missing/corrupt review
records to the same structured `409 review_record_unavailable`, and
`test_skill_api_maps_missing_release_records_to_structured_conflict` covers it.

Second independent Task 7 review found that publish-time unresolved review
findings were blocked but surfaced as `422` instead of the Task 7 release
precondition `409`. `backend/app/api/skills.py` now maps
`review_findings_unresolved` to `409`, and
`test_skill_api_maps_unresolved_review_findings_to_publish_conflict` covers a
bypassed durable review record through the public publish API.

## Gate Decision

Task 7 author/integrator self-check is ready for independent review. This is not
approval and does not complete Phase B by itself.

Post-remediation independent review returned `APPROVE`; see
`docs/review-notes/f014-task7-skill-api-review-response.md`.
