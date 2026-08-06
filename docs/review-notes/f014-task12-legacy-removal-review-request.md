---
feature_ids: [F014]
topics: [review-request, legacy-removal, task-12]
doc_kind: review-request
created: 2026-08-05
---

# F014 Task 12 Legacy Removal Review Request

Review-Target-ID: `f014-task12-legacy-removal`

Branch: `codex/skill-first-agent-runtime`

Required reviewer profile: independent reviewer; no self-review.

## Handoff

**What:** Review live Skill-first product replacement and old Workflow product
surface removal.

**Why:** F014 can only proceed when users create Tasks from immutable Skill
Versions and no live Workflow authoring/canvas/task-draft route remains.

**Tradeoff:** Generic runtime machinery remains because Task/Run execution still
uses existing Attempt, event, checkpoint, artifact, delivery, and cockpit code.

**Open Questions:** Look for live Workflow routes, stale frontend contracts that
import deleted files, legacy `prepare/run` client calls, and AI thread task-draft
creation.

**Next Action:** Return findings first with P0/P1/P2/P3 and exact file/line
references. End with `VERDICT: APPROVE` or `VERDICT: CHANGES_REQUESTED`.

## Inputs

- `docs/plans/2026-08-04-f014-skill-first-runtime.md` Task 12
- `docs/review-notes/f014-task12-legacy-removal-quality-gate.md`
- `backend/tests/test_skill_first_legacy_removal_gate.py`
- `frontend/scripts/*.test.mjs`
- `frontend/src/features/runs/run-cockpit-page.tsx`
- `frontend/src/features/tasks/task-wizard.tsx`
- `frontend/src/lib/api.ts`

