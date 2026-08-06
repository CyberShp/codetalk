---
feature_ids: [F014]
topics: [quality-gate, skill-review, skill-publish, task-6]
doc_kind: quality-gate-report
created: 2026-08-05
---

# F014 Task 6 Review And Publish Quality Gate

## Scope

This gate covers Task 6 only: review records, deterministic review findings,
patch proposal decisions, full-review-gated publication, and immutable Skill
Version artifacts. It does not expose Skill APIs, bind Tasks, execute Runs, run
Judge, implement UI, or remove Workflow product routes.

## Sub-Agent Ownership

| Agent | Spawn receipt | Ownership | Result |
|---|---|---|---|
| Ptolemy | `019fd1ca-e210-7670-86d1-101a0ab43e2a`, requested `gpt-5.6-terra` `medium` | `backend/app/services/skill_review.py`, `backend/tests/test_skill_review.py` only | Added review service and focused tests; reported `4 passed` |
| James | `019fd1ca-e2a5-73d2-bcab-ea0052e2f854`, requested `gpt-5.6-terra` `medium` | `backend/tests/test_skill_build_pipeline.py` only | Added publication/version RED contract; syntax check passed |
| Bernoulli | `019fd217-d7e6-7d40-8e11-9d665a7b5ed0` | `backend/tests/test_skill_build_pipeline.py` only | Added two seventh-round regression tests for tampered orphan audit recovery and stale publish lock recovery; reported `2 passed` after the production fix |
| Main integrator | current task, required shared-file owner | `skill_store.py`, `skill_build_pipeline.py`, shared integration and docs | Integrated durable review/version metadata and publish command |

The failed attempt to spawn a third read-only scanner hit the current child
Agent concurrency limit. It produced no work and no verdict.

## Contract Check

- Full and incremental review scopes are distinct.
- Seeded `must` / `must not` semantic contradictions produce deterministic P1
  findings.
- Review records retain non-secret provider, requested/effective/response
  model, output limit, context capacity, and session provenance.
- Review records are validated against `skill-review-v1` before persistence, so
  unknown fields such as credentials fail closed.
- Full-review records persist service-attested `reviewed_paths`; external
  records must provide matching `reviewed_file_digests` and cannot satisfy
  release by label alone.
- Review operations and patch decisions do not mutate Draft files.
- Patch proposals require explicit apply or reject decisions; apply is recorded
  as intent only.
- Patch proposals must match their target file digest, referenced finding IDs,
  and unified-diff target path.
- Build candidates do not publish Versions by themselves.
- `publish_build` is a separate explicit command.
- Publication requires a full Review decision of `approved` or `acknowledged`;
  incremental review alone is insufficient.
- Publication copies source, IR, validation, and review records into an
  immutable Version directory and writes a manifest linking `content_digest` and
  `review_evidence_digest`.
- Publication recomputes candidate content digest and review evidence digests
  before copying release bytes.
- Publication validates the staged release snapshot, including IR self-digest,
  after all release artifacts are assembled and staging contents are frozen.
- Publication verifies release review records, manifest, and source ZIP against
  expected in-memory or durable-store state after staging, after final move, and
  after DB metadata registration.
- Release directories are marked read-only after metadata registration; current
  process failures clean the staged/final release directory and leave no Version
  row.
- Cross-process publication uses deterministic Version IDs plus SQLite
  `BEGIN IMMEDIATE`; ordinary duplicate publishers return the existing Version
  instead of deleting each other's metadata.
- Acknowledged high-risk AI findings remain visible in release review evidence
  and are not converted into hidden deterministic blockers.
- Orphan final-directory recovery authenticates release audit files against
  durable store review records and patch decisions, not against orphan bytes.
- Publish lock directories record owner metadata and reclaim dead, invalid, or
  stale owners before retrying.
- Active publish-lock owners are not reclaimed; conflicting publishers fail with
  `publish_lock_timeout` and leave the owner record intact.

## Red-Green Evidence

Initial RED:

```text
cd backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q tests/test_skill_review.py
=> 4 failed because app.services.skill_review did not exist

cd backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q \
  tests/test_skill_store.py::test_record_review_retains_audit_metadata_without_mutating_draft_source
=> AttributeError: 'SkillStore' object has no attribute 'record_review'
```

Implementation fixes:

| Finding | Red evidence | Green evidence |
|---|---|---|
| Review service was missing | `test_skill_review.py` import failed | `SkillReviewService` supports full/incremental reviews, semantic contradictions, provenance, patch proposals, and patch decisions |
| Review metadata was not durable | `SkillStore.record_review` missing | `skill_reviews` stores review kind, decision, content digest, evidence digest, and record path while writing immutable review JSON |
| Review evidence accepted unknown fields | secret-like `api_key` in review evidence did not fail | `SkillReviewService.record_review` validates `skill-review-v1` before store persistence |
| Full review coverage was just a label | schema-valid external evidence could claim `review_kind=full` without file coverage | `record_review` attests full coverage from current candidate files and persists `reviewed_paths` |
| Full review coverage did not bind reviewed bytes | reviewer changed bytes during review and restored them before publish | review records now include `reviewed_file_digests`; `review_build` rejects candidate/source drift from build digest map before scanning |
| Publication could happen without review semantics | `publish_build` missing and Task 5 guaranteed no version publication | `SkillBuildPipeline.publish_build` requires required full review and rejects missing/incremental-only review |
| Version artifacts were not created | publication tests expected source package, source copy, IR, validation, review records, and manifest | version root now contains the expected immutable release artifact layout |
| DB version/build publication metadata could split across operations | self-review of pipeline showed separate insert/update operations | `record_published_version` inserts `skill_versions` and updates `skill_builds.version_id` in one SQLite transaction |
| Reviewed candidate bytes and review JSON could drift before publish | reviewer adversarial checks mutated candidate source and review record after approval | publish recomputes candidate digest, validates review schema, and recomputes review evidence digest |
| IR could be modified while keeping old embedded digest | reviewer changed IR `skill_id` but left `content_digest` unchanged | publish recomputes IR self-digest from the staged snapshot and rejects `ir_digest_mismatch` |
| Staged bytes could change after validation but before ZIP/DB | reviewer mutated staging during ZIP generation | publish now assembles review records and source ZIP first, freezes staging contents, then validates the exact staged source/IR/validation snapshot before DB registration |
| Staged bytes could change after validation during store callback | reviewer mutated staging after final validation | publish now validates immediately before final move and again after final move/read-only marking; review bytes are also compared to final source before DB metadata registration |
| Patch decisions could disappear from release audit | reviewer found decisions persisted in SQLite but absent from Version review records | release review records include `patch_decisions` for each review |
| Patch proposals were not bound to candidate state | reviewer found stale base, unknown finding, and wrong diff target were accepted | patch validation now checks `base_digest`, finding IDs, and diff headers |
| Patch diffs could include extra file edits | reviewer included a valid declared target plus an undeclared second file | diff validation now parses all old/new file headers and requires exactly the declared target |
| Plain unified diff headers were ignored | reviewer included `--- notes.md` / `+++ notes.md` after a valid git-style target | diff parsing recognizes both git-style and plain unified-diff file headers |
| Losing duplicate publisher could delete winner metadata | reviewer simulated deterministic-ID race | SQLite `BEGIN IMMEDIATE` serializes publication metadata and rollback deletes are constrained to the owning build/version |
| Orphan recovery trusted tampered orphan audit bytes | reviewer replaced release review records with `[]` and adjusted manifest `review_records=[]` | recovery rebuilds expected review records from durable store state and rejects mismatched orphan review/manifest files |
| Process termination left unrecoverable publish lock | reviewer left a lock directory before metadata registration | lock `owner.json` records pid/build/version/timestamp; invalid, dead, or stale owners are reclaimed |
| Stale-lock recovery could overreach and delete an active owner | self-check added active current-pid lock scenario | publish raises `publish_lock_timeout`, leaves the active lock intact, and creates no Version row |
| Aged active owner lock could still be reclaimed | independent re-review flagged age-before-PID logic | active owner PID now prevents reclamation even when the lock age exceeds the stale threshold |
| Stale lock reclaim had a TOCTOU deletion window | independent re-review flagged check-then-delete against an unbound lock | reclaim rereads `owner.json` and only deletes the lock when the owner snapshot is unchanged |

## Fresh Verification

Worktree: `/Volumes/Media/codetalk-skill-first-agent-runtime`

Branch: `codex/skill-first-agent-runtime`

```text
cd backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q \
  tests/test_skill_store.py \
  tests/test_skill_review.py \
  tests/test_skill_build_pipeline.py
=> 28 passed

cd backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q \
  tests/test_skill_schemas.py \
  tests/test_skill_package_importer.py \
  tests/test_skill_package_validator.py \
  tests/test_skill_ir_compiler.py \
  tests/test_skill_store.py \
  tests/test_skill_build_pipeline.py \
  tests/test_skill_review.py
=> 333 passed, 1 skipped

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
  tests/test_skill_review.py
=> 336 passed
```

Additional checks:

- scoped Python compilation: pass.
- `git diff --check`: pass.

## Gate Decision

Task 6 author/integrator self-check is ready for independent review. This
report is not approval and does not complete Phase B by itself.
