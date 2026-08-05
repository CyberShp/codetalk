---
feature_ids: [F014]
topics: [review-request, safe-zip, skill-importer, task-3]
doc_kind: review-request
created: 2026-08-04
---

# F014 Task 3 Safe Importer Review Request

Review-Target-ID: `f014-task3`

Branch: `codex/skill-first-agent-runtime`

Required reviewer profile: `gpt-5.6-sol`, reasoning `high`.

## Original Requirements

Source: `/Users/shepard/Downloads/codetalk_skill_first_refactor_plan.md` and
`docs/features/F014-skill-first-runtime.md` AC-A2/AC-A3.

> Multi-scenario upload becomes a Pack of independent Skills. The supplied ZIP
> must retain exact paths and files; deterministic structural errors block
> progress. Unknown or unsafe package content must not silently mutate Drafts.

## Handoff

**What:** Review Task 3's two production modules and importer tests as an
untrusted-archive security boundary. Verify the real pinned archive as well as
adversarial synthetic ZIPs.

**Why:** Importer mistakes would let traversal, symlink, collision, ZIP bomb,
partial extraction, or digest drift become the input authority for every later
compiler and release stage.

**Tradeoff:** The importer produces source-scenario declarations, not terminal
Skill IDs or copied Draft roots. Same-parent rename has a documented portable
no-replace limitation; Task 5 owns interprocess serialization.

**Open Questions:** Look specifically for ZIP parser differentials,
header-versus-read limit gaps, root detection ambiguity, source/destination
TOCTOU, private archive snapshot provenance, staging leaks, silent Unicode
normalization, incomplete real-archive evidence, and tests that pass without
exercising production behavior.

**Next Action:** Return findings first with P0/P1/P2/P3 and exact file/line
references. End with `APPROVE` or `CHANGES_REQUESTED`. Do not edit files or
commit. Task 4 remains blocked until the verdict is `APPROVE`.

## Review Inputs

- `backend/app/services/skill_package_paths.py`
- `backend/app/services/skill_package_importer.py`
- `backend/tests/test_skill_package_importer.py`
- `backend/tests/test_skill_source_inventory.py`
- `backend/tests/fixtures/skills/codetalks-v2.4/source-inventory.json`
- `docs/features/F014-skill-first-runtime.md`
- `docs/plans/2026-08-04-f014-skill-first-runtime.md`
- `docs/review-notes/f014-task3-importer-quality-gate.md`

## Verification

```bash
cd /Volumes/Media/codetalk-skill-first-agent-runtime
CODETALKS_V24_ARCHIVE=/Users/shepard/Downloads/codetalks-fused-v2.4-zh.zip \
  PYTHONPATH=backend uv run --python 3.12 \
  --with-requirements backend/requirements.txt pytest -q \
  backend/tests/test_skill_schemas.py \
  backend/tests/test_skill_source_inventory.py \
  backend/tests/test_skill_package_importer.py
```

Expected: `254 passed`, no skips or warnings.
