---
feature_ids: [F014]
topics: [review-response, skill-review, skill-publish, task-6]
doc_kind: review-response
created: 2026-08-05
---

# F014 Task 6 Review And Publish Review Response

## Decision

Task 6 received independent `APPROVE` after remediation loops.

Initial reviewer: Meitner (`019fd1d4-57ae-7d90-9699-96c4d0cb377c`)

Initial verdict: `CHANGES_REQUESTED`

Final reviewer: Singer (`019fd2b5-e77d-7e33-baab-0aca6380e4d7`)

Final verdict:

```text
No Task 6 issues found in the P1 remediation. The prior release escape is
closed at both entry and publish time.

VERDICT: APPROVE
```

## Remediation Summary

| Review finding | Response |
|---|---|
| P1 publication could release bytes changed after review | Added a red test mutating candidate source after approval; `publish_build` now recomputes candidate content digest from current source, IR, validation, and artifact layout before release. |
| P1 full review was only a caller label | Added `reviewed_paths` to `skill-review-v1`; `record_review` now attests full coverage from current candidate files and rejects mismatched coverage. |
| P1 review evidence could drift from digest | Added a red test mutating stored review JSON; publish now validates review schema and recomputes evidence digest against SQLite metadata. |
| P1 published artifacts were writable | Added a red test writing to published source; release trees are chmod read-only after metadata registration. |
| P1 DB/filesystem partial publication and duplicate publish | Added DB-registration failure cleanup and duplicate publish tests; current-process failures remove version dirs and rows remain absent, and repeat publish returns the existing Version. |
| P2 patch decisions were not in release audit | Release review records now include durable `patch_decisions` from SQLite. |
| P2 patch proposals were not bound to file/finding/diff | Patch validation now checks target file existence, current `base_digest`, referenced finding IDs, and diff headers. |
| Second-round P1 IR and source could change around verification/copy | Publish now validates the staged release snapshot after copy, recomputes IR self-digest, and records deterministic version IDs. |
| Second-round P1 full review was not byte-bound | Review records now require `reviewed_file_digests`; `review_build` rejects candidate drift from build digest map before scanning. |
| Second-round P1 duplicate/cross-process publish left extra roots | Version IDs are deterministic per build; if DB returns an existing Version, the losing publisher removes its staging root and returns the existing row. |
| Second-round P2 diff validation allowed undeclared files | Diff validation parses all `--- a/` and `+++ b/` headers and requires exactly the declared target. |
| Third-round P1 staged bytes could change after validation | Publish now writes review records and source ZIP, freezes staging contents, then recomputes candidate and IR digests from the staged source/IR/validation snapshot before metadata registration. |
| Third-round P1 `record_review` could bind to changed build bytes | `record_review` now runs the same candidate/build digest-map drift check as `review_build`; publish also compares review `reviewed_file_digests` against staged release source bytes. |
| Third-round P1 duplicate loser could delete winner | Publish checks existing final root before and after DB registration and only removes a final root after this process moved it. |
| Third-round P2 plain unified diff headers were ignored | `_diff_targets` now recognizes both git-style `--- a/` / `+++ b/` and plain `--- path` / `+++ path` headers. |
| Fourth-round P1 staged source could change after validation | Publish validates the staged snapshot immediately before move and validates the final read-only tree again before writing metadata. |
| Fourth-round P1 duplicate loser could delete winner metadata | Store publication now uses SQLite `BEGIN IMMEDIATE`; rollback deletes are constrained by build/version, and pipeline returns existing Versions when a final root already has metadata. |
| Fourth-round P1 DB row could point to missing final artifacts | Pipeline now moves and chmods the final release tree before DB metadata registration, then rolls back files on DB failure. A process death in the opposite window leaves a final directory without DB metadata, which later publish attempts fail closed as `publication_incomplete` instead of creating a DB row pointing to absent files. |
| Fifth-round P1 final bytes could change during store callback | Pipeline validates final source/IR/validation and review byte bindings again after metadata callback and rolls back metadata/files on mismatch. |
| Fifth-round P1 staging failure could delete winner final tree | Staging exception cleanup no longer removes `final_root`; final cleanup is gated by a `moved_final` flag. |
| Fifth-round P1 post-commit read failure could leave DB without artifacts | `record_published_version` returns the committed `SkillVersion` value directly and no longer performs a post-commit `get_version` read. |
| Fifth-round P2 valid duplicate move race returned an error | Move `OSError` now waits briefly for an existing deterministic Version and returns it when the winner commits. |
| Fifth-round P2 final-dir-without-DB orphan was unrecoverable | A retry that sees final artifacts without DB metadata validates the final tree and records the missing metadata instead of permanently failing. |
| Sixth-round P1 review records, manifest, and source ZIP could be mutated by metadata callback | Added `_verify_release_bundle`: post-callback validation now compares review-record JSON to the in-memory release audit, compares manifest JSON to expected manifest, and validates the source ZIP member list and digests against final source. Recovery uses the same bundle verifier. |
| Sixth-round P1 duplicate timeout could still roll back winner metadata | Publish now takes a deterministic filesystem lock directory for the whole publish section, so ordinary cross-process publishers serialize before final move and DB metadata. |
| Sixth-round evidence hygiene warning from read-only temp releases | Tests restore temporary release permissions after asserting immutability, keeping pytest cleanup clean without weakening production read-only release behavior. |
| Seventh-round P1 orphan recovery authenticated review evidence against the orphan itself | Added a red recovery test that tampers `reviews/skill-reviews.json` and manifest `review_records`; recovery now rebuilds expected review records from durable SQLite review/patch-decision state and rejects orphan bundles whose audit files differ. |
| Seventh-round P1 process termination left unrecoverable filesystem locks | Added a stale-owner lock recovery test; publish locks now write `owner.json` with pid/build/version/timestamp and reclaim dead, invalid, or stale owners before retrying. |
| Seventh-round active-lock safety gap | Added an active-owner lock test proving a current-process, non-stale lock times out and remains intact instead of being reclaimed. |
| Eighth-round P1 aged active locks could still be reclaimed | Changed reclaim ordering so live owner PIDs are never reclaimed solely because the lock is old; the active-owner regression now uses an aged current-pid lock. |
| Eighth-round P1 stale-lock reclaim could delete a fresh owner | Added a TOCTOU regression that swaps `owner.json` between stale detection and removal; reclaim now rereads the owner bytes and only deletes when the inspected snapshot is unchanged. |
| Ninth-round P1 approved review could still carry open findings | Added `record_review` validation and publish-time defensive validation so `approved` reviews cannot contain findings, and `acknowledged` reviews require every finding to be `acknowledged` or `resolved`. |

## Post-Remediation Evidence

```text
cd backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q \
  tests/test_skill_store.py \
  tests/test_skill_review.py \
  tests/test_skill_build_pipeline.py
=> 30 passed

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
=> 339 passed
```

Additional checks:

- scoped Python compilation: pass.
- `git diff --check`: pass.

## Next Action

Task 6 may proceed to merge gate. Later gates must still verify Task binding,
runtime execution, Judge, and legacy removal on the final SHA.
