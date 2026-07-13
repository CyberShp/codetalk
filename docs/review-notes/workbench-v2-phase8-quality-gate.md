---
feature_ids:
  - workbench-v2
topics:
  - quality-gate
  - release
  - acceptance
doc_kind: review-note
created: 2026-07-13
---

# Workbench V2 Phase 8 Quality Gate

## Vision and scope

- Authority checked: `codetalk_workbench_v2_refactor_plan.md`, the explicit Goal contract, the
  baseline at `a38e884033dc3d18715e051097af351cb0e7ec3a`, and current compatibility/security tests.
- Phase 0 through Phase 8 were implemented in order on the isolated `codex/workbench-v2` branch.
- The product invariants are preserved: Workflow authoring and Task creation are separate; the
  canvas graph is authoritative; Published Versions and started Attempts are immutable; Task and
  Attempt identity are separate; execution, quality, and delivery have independent outcomes.
- The implementation does not add plan-excluded cron, approval, arbitrary script, loop, marketplace,
  multi-user ownership, vector database, or LLM merge capabilities.

## Acceptance mapping

- Workflow Header/Version migration, immutable publication, graph validation, deterministic compile,
  frozen Execution Plans, and dependency-aware scheduling are covered by Phases 1-3 and their unit,
  API, migration, contract, and browser tests.
- The six-step Workflow and Task wizards expose named inputs, Agent/Skills/MCP resources, output
  contracts, graph dependencies, validation, trial run, publication, effective overrides, and run
  launch without requiring editable JSON.
- Tasks own multiple immutable Attempts. Retry creates a child Attempt and can reuse successful
  parent nodes without overwriting history.
- The run cockpit consumes real append-only events, separates public output from diagnostics, shows
  failure/recovery state, previews/downloads real artifacts, and bounds long event histories.
- Semantic and Evidence libraries use the existing stores and provide list/filter/detail/edit,
  lifecycle, preview-before-import, conflict handling, provenance, and source-slice inspection.
- V2 is the default entry. `WORKBENCH_V2_ENABLED=false` retains a one-release legacy rollback. Old
  APIs, rows, runs, events, artifacts, semantic cases, and evidence remain readable in place.
- SQLite migration is additive, idempotent, and preceded by a verified one-time SQLite backup.

## Fresh verification evidence

- Backend full suite after final independent-review remediation: `2260 passed, 8 skipped` in
  `1232.35s`.
- Phase 8 release/migration and characterization focus: `8 passed`.
- Frontend ESLint with zero warnings: passed.
- `tsc --noEmit`: passed.
- Next.js production build: passed; V2 and compatibility routes are present.
- Frontend static contracts: `45 passed` across five contract files after the second review fixes.
- Real Chromium V2 journeys: `10 passed` in `23.9s`, including direct-route rollback against a
  dedicated backend with `WORKBENCH_V2_ENABLED=false`.
- The workflow browser journey used real typing, keyboard focus/Delete/undo, mouse drag, server
  validation/compile, trial run, and publication: `1 passed` in `3.9s`.
- Desktop containment was inspected at 1440, 1280, and 1024 widths; no page-level horizontal
  overflow or double main scrolling was found. Existing Phase 6/7 mobile checks remain green.
- A production browser materialized 200 nodes in `5077ms`; keyboard focus worked and a real mouse
  drag took `92ms`, moving the node 50px.
- Secret scan found only intentional synthetic fixtures. `git diff --check` passed and no generated
  image/log/trace/archive appears as an untracked repository-root artifact.

## Compatibility and release judgment

- Phase 8 asks to delete old UI only after confirming it has no callers, while the same phase requires
  an effective legacy rollback. The old controller still has one intentional dynamic rollback caller,
  so deleting it would falsify the rollback acceptance criterion. D015 records the decision to retain
  it for one release and exclude it from the default bundle path.
- The old API exports remain intentionally available for one release. Active Semantic/Evidence V2
  domains use split clients and types; compatibility exports do not receive new V2 ownership.
- Windows command resolution has automated coverage, but no Windows real-machine acceptance is
  claimed by this local macOS gate. This does not alter the Workbench V2 product contract.

## Hygiene and residual risk

- The base worktree contains only its pre-existing untracked `.agents/` directory; this goal's files
  are confined to the dedicated worktree.
- Repository-specific hotfix/fallback-layer scripts are absent, so those checks are not applicable.
- No `.pen` source exists in the repository; visual acceptance uses the supplied plan and real-browser
  screenshots instead of claiming a Pencil-source update.
- Remaining release risk is environmental rather than an unimplemented Workbench capability:
  production provider credentials, Windows native runtime behavior, and very large real repositories
  still require deployment-specific smoke testing.

## Gate result

The first independent review correctly rejected the release with five P1, four P2, and one P3
finding. Its first re-review found one additional P1 at the legacy-event read boundary and one P2 in
the pause/reconnect combination. Both were reproduced with red tests and fixed: every historical
event is redacted again when read, and pause owns an immutable visible-event snapshot while live
refreshes merge into the bounded active window. Focused backend regression is `14 passed`; frontend
lint, type check, production build, 45 contracts, and 10 real Chromium journeys are green. Self
quality gate: **PASS**. The same independent reviewer reran focused backend/frontend checks, found no
remaining P0/P1/P2, and returned **APPROVED**. The only P3 is the absence of browser-level SSE fault
injection; this is non-blocking because the pause snapshot/recovery paths have direct contracts and
normal real-browser coverage. No P0, P1, or P2 is waived.
