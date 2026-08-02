---
feature_ids: [F004, F005, F006, F007, F008, F009, F010, F011]
topics: [workflow, desktop, artifacts, coverage, test-knowledge]
doc_kind: implementation-plan
created: 2026-08-01
---

# F004-F011 Iteration Program

## Goal

Make workflow runs the normal CodeTalk testing path, with typed reusable inputs,
a quiet run cockpit, predictable and locally customizable deliverables, coverage
as a workflow capability, and an evidence-safe local experience knowledge center.

This is one product program with separately closeable gates. A passing gate for
one feature cannot be used as evidence that another feature is complete.

## Current Release Scope

On 2026-08-01 the user selected frontend/backend Web deployment as the current
release mode and explicitly deferred Windows packaging and smoke. Gates 1-4
therefore define completion for this release. Desktop code is excluded from the
active branch and remains a future distribution track.

## Acceptance Boundary

- F004: the workspace report launch, AI test-activity handoff, coverage launch,
  and retest/rerun entry create or open a workflow run; built-in coverage-gap
  and defect-retest workflows exist. General chat, source browsing, settings,
  and knowledge maintenance are explicitly not test-activity entries.
- F005: input definitions carry labels, examples, missing-input guidance, file
  constraints, and reusable prior-run values, with backend validation.
- F006: default run UI exposes current step, waiting/failure reason, recovery,
  and artifacts; logs stay folded; browser tests cover live state transitions.
- F007: deferred by user and excluded from this Web release. No desktop host,
  installer, updater, rollback flow, or Windows smoke evidence is claimed.
- F008: every successful run has a stable envelope (`summary.md`,
  `manifest.json`, `artifact_validation.json`) and one user deliverable ZIP;
  typed workflow artifacts remain workflow-specific.
- F009: workflow coverage parsing has parity with retained backend formats and
  no user-facing `/coverage` route remains.
- F010: versioned local profiles resolve by run selection, workspace binding,
  ordered explicit run feature tag, user default, then built-in default;
  profiles do not merge and cannot weaken evidence or path safety.
- F011: typed incident/pattern/source/import contracts, deterministic paste,
  DOCX, text-PDF and XLSX ingestion, federated retrieval, explicit-MR-only
  enrichment contracts, management UI, provenance artifacts, and replay gates
  are implemented without multi-user or external-vector infrastructure.

## Architecture Decisions

- Reuse the existing workflow DSL, task-run preparation, artifact manifest,
  Evidence Memory, semantic library, and material retrieval stores.
- Add narrow stores instead of expanding the `agent_workbench.py` monolith:
  `artifact_profiles.py`, `knowledge_store.py`, `knowledge_ingest.py`, and
  `knowledge_retrieval.py`.
- Use SQLite/FTS5 plus bounded optional embedding reranking. Use `python-docx`,
  `pypdf`, and `openpyxl`; text PDF provenance is page plus ordered text run,
  while scanned PDFs return `needs_ocr`.
- Defer desktop distribution, updater, and rollback design until the Web release
  has completed intranet validation.
- Historical similarity is always an investigation lead. Only current,
  validated evidence may promote a current-code finding.

## Delivery Sequence

### Gate 1: Workflow Path and Coverage Parity

1. Add failing preset and DSL tests for `coverage_gap`, `defect_retest`, input
   labels/examples/guidance, prior-run reuse, and knowledge-policy validation.
2. Implement the DSL fields and presets in
   `backend/app/services/workflow_dsl.py` and `workflow_presets.py`.
3. Adapt workflow coverage parsing to the retained coverage service formats;
   add LCOV, Cobertura, JaCoCo, HTML, and function-hit fixtures.
4. Replace workspace legacy report/coverage launch behavior with Phase2
   workflow-run entry points in `frontend/src/features/tasks/task-wizard.tsx`
   and the workspace detail page.
5. Add live Playwright coverage for ready, waiting, failed, retrying, and
   completed cockpit states.

Verification:

```text
cd backend && pytest -q tests/test_workflow_presets.py tests/test_workbench_task_run.py tests/test_agent_workbench_api.py
cd frontend && npm run lint && node scripts/workbench-run-ui-contract.test.mjs
cd frontend && npx playwright test e2e/agent-workbench.spec.ts --project=chromium
```

### Gate 2: Artifact Envelope and Profiles

1. Add failing tests for standard envelope generation, user ZIP contents,
   profile create/version/restore, resolution precedence, unique artifact ids
   and filenames, single-profile non-merging, deterministic tag order, and
   immutable safety.
2. Implement `backend/app/services/artifact_profiles.py`; expose local profile
   CRUD, versions, restore, workspace binding, and sample validation APIs.
3. Resolve and snapshot the effective profile before task preparation; inject
   it into Agent contracts and validate materialized artifacts.
4. Make `artifact_export` create the standard envelope and deliverable ZIP.
5. Add profile management and run selection to the existing Workbench UI.

Verification:

```text
cd backend && pytest -q tests/test_artifact_profiles.py tests/test_workbench_artifact_manifest.py tests/test_workbench_task_run.py
cd frontend && npm run lint && npm run build
cd frontend && npx playwright test e2e/agent-workbench.spec.ts --project=chromium
```

### Gate 3: Knowledge Contracts, Ingestion, and Retrieval

1. Add failing store tests for source hashes, incidents, many-to-many pattern
   links, reversible pattern versions, exact dedupe, feedback, FTS scopes, and
   workspace identity. Exact canonical remote matches may be project scoped;
   unknown, ambiguous, or mismatched identities must become personal-global.
2. Implement `knowledge_store.py` with forward schema migration and backup.
3. Add parser fixtures and failing locator tests; implement deterministic paste,
   DOCX, text-PDF, and XLSX ingestion in `knowledge_ingest.py`.
4. Serialize an explicit-MR enrichment request only when the user supplied an
   MR; cap direct references at one hop and emit `source_manifest.json`.
   Contract tests prove that no MR creates no CodeHub request, and validation
   rejects second-hop, keyword-search, and unmanifested returned sources. The
   gate validates the request and returned ancestry, not private MCP internals.
5. Implement federated retrieval across experience patterns, semantic cases,
   materials, and Evidence Memory, preserving source authority and degraded
   paths. Integrate workflow `knowledge_policy` at preparation and follow-up.
6. Emit `knowledge_retrieval.json` and `knowledge_usage.json`; enforce that a
   historical or unreviewed pattern creates only `investigation_lead`, current
   supporting evidence is required for `candidate_finding`, current evidence
   plus recorded disconfirming checks are required for `confirmed_finding`, and
   a proven fallback produces `ruled_out`.
7. Add Historical Incidents, Experience Patterns, and Import Jobs tabs to the
   existing secondary knowledge view.

Verification:

```text
cd backend && pytest -q tests/test_knowledge_store.py tests/test_knowledge_ingest.py tests/test_knowledge_retrieval.py tests/test_workbench_task_run.py
cd frontend && npm run lint && npm run build
cd frontend && npx playwright test e2e/agent-workbench.spec.ts --project=chromium
```

### Gate 4: Historical Replay

1. Encode the two supplied iSCSI incidents as source-backed replay fixtures:
   command-resource/CmdSN non-recovery and DTOE login-window shared-queue
   amplification.
2. Add a misleading local-lock fixture whose release occurs through a separate
   function/caller path.
3. Assert bounded retrieval usefulness and explicit conclusion transitions:
   history-only remains a lead, current support may become a candidate,
   confirmation requires recorded disconfirming checks, and an existing
   fallback becomes ruled out.
4. Produce a machine-readable replay report with retrieval and conclusion
   precision dimensions.

### Gate 5: Windows Desktop Runtime (Deferred)

This gate is intentionally outside the active branch. It will be redesigned
after intranet Web validation and cannot be inferred complete from the deployer
or from macOS tests.

## Agent Allocation

- Sol High owns architecture, integration, final evidence, and goal closure.
- Luna High owns bounded implementation and test slices with disjoint files.
- Terra High owns complex cross-layer work and independent review.
- An author never reviews the same slice. Any post-review Sol changes return to
  a fresh reviewer for the modified scope.

## Program Completion Evidence

The current Web release closes when Gates 1-4 are green, the backlog links each
included feature to test or artifact evidence, the frontend/backend runtime is
healthy, and an independent reviewer reports no unresolved blocking finding.
F007 remains deferred by explicit user decision; completing it later still
requires the real Windows evidence listed in Gate 5.
