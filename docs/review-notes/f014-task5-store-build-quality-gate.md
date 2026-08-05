---
feature_ids: [F014]
topics: [quality-gate, skill-store, skill-build, task-5]
doc_kind: quality-gate-report
created: 2026-08-05
---

# F014 Task 5 Store And Build Quality Gate

## Scope

This gate covers Task 5 only: filesystem-authoritative mutable Skill Drafts,
SQLite metadata for Skill Projects/Drafts/Builds, and deterministic build
candidates. It does not publish immutable Skill Versions, run AI Review, apply
review patches, expose APIs, bind Tasks, execute Runs, run Judge, or replace
the frontend Workflow product path.

## Contract Check

- Draft source bytes live under `data_dir/skills/drafts/<draft_id>/source`.
- SQLite stores metadata and paths only; Draft source, IR, and content bytes are
  not duplicated as database JSON.
- External filesystem edits are observed by `rescan_draft`.
- Build candidates produce an unpacked copy, candidate ZIP, IR, validation
  report, file digest map, manifest, deterministic content digest, and ZIP
  digest.
- Build candidates require review and never create rows in `skill_versions`.
- Deterministic validation failures record a failed build plus validation report
  and do not leave a candidate ZIP.
- No object-store abstraction was added.

## Red-Green Evidence

Initial RED:

```text
PYTHONPATH=backend uv run --python 3.12 \
  --with-requirements backend/requirements.txt pytest -q \
  backend/tests/test_skill_store.py \
  backend/tests/test_skill_build_pipeline.py
=> 6 failed
```

The failures were expected `ModuleNotFoundError` failures for missing Task 5
modules.

Implementation fixes:

| Finding | Red evidence | Green evidence |
|---|---|---|
| No filesystem-authoritative Skill Draft store existed | imports of `app.services.skill_store` failed | `SkillStore` creates project/draft metadata, copies source to Draft filesystem root, and rescans external edits |
| No deterministic build candidate pipeline existed | imports of `app.services.skill_build_pipeline` failed | `SkillBuildPipeline` emits source copy, IR, validation report, digest map, manifest, deterministic ZIP, content digest, and ZIP digest |
| Build failure could remain in `building` state during store adapter fallback | validation-failure test read `failed.status == "building"` | missing digests are normalized before SQLite fallback updates, so failed builds persist as `failed` |
| Candidate content digest was not distinct from IR digest | manifest lacked `ir_content_digest` and reused the IR digest as the candidate digest | manifest now links `ir_content_digest` separately, while `content_digest` covers the build candidate contract, source digest map, validation report, and artifact layout |
| Unexpected artifact generation failures left partial staging and a `building` row | forcing deterministic ZIP writing to raise left `.build_*.tmp-*` contents and durable `building` metadata | generic build failures now clean staging/final roots, record a failed build with validation report, leave no ZIP, and raise `SkillBuildError(code="build_failed")` |

## Fresh Verification

Worktree: `/Volumes/Media/codetalk-skill-first-agent-runtime`

Branch: `codex/skill-first-agent-runtime`

```text
PYTHONPATH=backend uv run --python 3.12 \
  --with-requirements backend/requirements.txt pytest -q \
  backend/tests/test_skill_store.py \
  backend/tests/test_skill_build_pipeline.py
=> 8 passed

PYTHONPATH=backend uv run --python 3.12 \
  --with-requirements backend/requirements.txt pytest -q \
  backend/tests/test_skill_schemas.py \
  backend/tests/test_skill_source_inventory.py \
  backend/tests/test_skill_package_importer.py \
  backend/tests/test_skill_package_validator.py \
  backend/tests/test_skill_ir_compiler.py \
  backend/tests/test_skill_store.py \
  backend/tests/test_skill_build_pipeline.py
=> 315 passed, 2 skipped

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
=> 317 passed
```

Additional checks:

- scoped Python compilation: pass.
- `git diff --check`: pass.

## Gate Decision

Task 5 author/integrator self-check is ready for independent review. This
report is not approval and does not authorize Task 6 by itself.
