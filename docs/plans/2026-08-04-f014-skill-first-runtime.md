---
feature_ids: [F014]
topics: [implementation-plan, skill-first, acceptance, subagents, tdd]
doc_kind: plan
created: 2026-08-04
---

# F014 Skill-first Product and Runtime Implementation Plan

**Feature:** F014 - `docs/features/F014-skill-first-runtime.md`
**Goal:** Replace the Workflow product path with a complete, immutable, verifiable Skill-first path while preserving the supplied Codetalks v2.4 semantics.
**Acceptance Criteria:** AC-A1 through AC-E6 in the F014 spec.
**Architecture:** Compile mutable Skill Project files into immutable Skill Versions and a terminal Skill IR. Task Run orchestrates the frozen Skill Invocation through the existing Harness, Attempt, event, checkpoint, artifact, cancellation, recovery, and cockpit mechanisms on main.
**Tech Stack:** Python 3.11+, FastAPI, Pydantic, JSON Schema 2020-12, SQLite, filesystem ZIP artifacts, pytest, Next.js, TypeScript, Playwright.
**Frontend validation:** Yes - browser operation, screenshots, and responsive checks are mandatory.

---

## 1. Finish Line

The feature is complete only when the supplied archive imports as five Skills,
the module-analysis Skill publishes immutably, a Task directly binds it, a real
CodeAgent executes all nine steps, an independent Judge controls READY, selected
delivery filters presentation rather than execution, restart/cancel are durable,
and no live Workflow product path or professional `ai_staged_execution` entry
remains.

We are not building dynamic pruning, multi-Skill orchestration, F012/F013
capabilities, a marketplace, multi-user control, object storage, or a new
distributed runtime.

## 2. Terminal Contracts

Create under `backend/app/schemas/skills/`:

1. `codetalk-skill-v1.schema.json`
2. `codetalk-skill-pack-v1.schema.json`
3. `skill-ir-v1.schema.json`
4. `skill-review-v1.schema.json`
5. `skill-run-invocation-v1.schema.json`
6. `agent-capability-report-v1.schema.json`

Stable IDs are semantic (`step.flow_analysis`, `artifact.flow_model`,
`delivery.full_report`) and never derived from filenames. The Run Invocation is
the only frozen bridge from Skill domain to runtime. It owns the Skill digest,
input snapshot reference, selected deliveries, runtime/capability report,
session reference, artifact root, and Judge declaration.

## 3. Acceptance Strategy

### 3.1 Deterministic archive and contract gate

Pin the source SHA-256 and create a checked-in minimal fixture with the same
semantic topology. Test archive traversal, absolute paths, symlinks, duplicate
normalized Unicode paths, invalid encodings, missing references, duplicate IDs,
cycles, missing artifact producers, unconsumed outputs, undeclared scripts, and
multi-scenario Skill rejection.

Golden assertions for the supplied source:

- 37 archive files accounted for;
- five independent scenario Skills in one Pack;
- module analysis contains nine ordered steps;
- three mandatory core-rule acknowledgements;
- 37 required artifact declarations;
- eight formal outputs;
- required depth Judge with isolated-session policy;
- `run_guard.py` declared with timeout, cwd, exit code, logs, and write scope.

### 3.2 Build and immutability gate

Build twice from identical bytes and compare IR, package ZIP, file digest map,
and release digest. Mutation of a released path must fail. External Draft edits
must appear only after rescan. AI patches are stored as proposals and require an
explicit apply decision followed by a new deterministic build.

### 3.3 Runtime contract gate

Use a deterministic fake Agent to emit messages, tool events, artifacts,
waiting state, cancellation, failure, and completion. Assert ordered persisted
events, frozen invocation, checkpoint-before-projection, idempotent cancellation,
clean process termination, restart replay, and no access to mutable Draft files.

### 3.4 Real vertical gate

Run `codetalks-module-full-analysis` with the company CodeAgent against a local
source/design fixture. Record source SHA, Skill Version/digest, invocation,
capability report, event log, checkpoint, artifact manifest, Producer session,
Judge session, and delivery package. No secrets or environment-specific paths
may enter committed evidence.

### 3.5 Product and removal gate

Operate Task creation, Skill/Pack views, Run Cockpit, blocked/degraded state,
Judge transition, delivery selection, cancel, and restart in a real browser.
After the vertical gate is green, source/API/route tests must prove the old
Workflow product surface is absent without deleting reusable runtime machinery.

## 4. TDD Delivery Sequence

### Task 0: Architecture boundary and asset matrix

**Files:**

- Create: `docs/decisions/adr-027-skill-first-product-model.md`
- Create: `docs/decisions/adr-028-skill-build-release-review.md`
- Create: `docs/decisions/adr-029-skill-runtime-boundary.md`
- Create: `docs/contracts/AGENT_RUNTIME_CONTRACT.md`
- Create: `docs/plans/skill-first-existing-asset-matrix.md`

Freeze the three terminal decisions before production code: the product model,
the build/release/review authority model, and the Skill-to-runtime boundary.
The Runtime Contract must describe only capabilities available from main or
explicitly required by F014; it must not copy F013 code or event vocabulary.
The asset matrix classifies every Workflow, Task, Run, Harness, checkpoint,
event, artifact, delivery, cockpit, and staged-analysis module as reuse, adapt,
remove, or exclude, with caller/callee evidence.

### Task 1: Source-to-target trace fixture

**Files:**

- Create: `backend/tests/fixtures/skills/codetalks-v2.4/source-inventory.json`
- Create: `backend/tests/fixtures/skills/codetalks-v2.4/expected-ir-summary.json`
- Create: `backend/tests/test_skill_source_inventory.py`

Write failing tests for the pinned archive inventory, UTF-8 filenames, scenario
split, step/artifact/output counts, and source-to-IR traceability. The test may
use a caller-provided archive path locally; CI uses the checked-in minimal
semantic fixture and never depends on Downloads.

### Task 2: Six schemas and adversarial fixtures

**Files:**

- Create: `backend/app/schemas/skills/*.schema.json`
- Create: `backend/tests/fixtures/skills/contracts/`
- Create: `backend/tests/test_skill_schemas.py`
- Modify: `backend/requirements.txt`

Write positive and negative fixtures first, verify schema failures, then add the
minimal schemas. Unknown terminal fields fail closed. Schema IDs and references
must resolve without network access.

### Task 3: Safe importer and Pack split

**Files:**

- Create: `backend/app/services/skill_package_importer.py`
- Create: `backend/app/services/skill_package_paths.py`
- Create: `backend/tests/test_skill_package_importer.py`

Reject unsafe archives before extraction, normalize and retain UTF-8 paths,
produce an inventory with content hashes, and split the five source workflow
variants into independent draft Skills. Do not infer scenarios from prose when
the source manifest already declares them.

### Task 4: Deterministic validator and IR compiler

**Files:**

- Create: `backend/app/services/skill_package_validator.py`
- Create: `backend/app/services/skill_ir_compiler.py`
- Create: `backend/tests/test_skill_package_validator.py`
- Create: `backend/tests/test_skill_ir_compiler.py`

Validate references, IDs, dependencies, producers/consumers, outputs, scripts,
Judge contract, and file paths. Compile only validated input. Golden tests bind
every IR field to a source file or explicit deterministic default.

### Task 5: Skill store and immutable build

**Files:**

- Create: `backend/app/services/skill_store.py`
- Create: `backend/app/services/skill_build_pipeline.py`
- Create: `backend/tests/test_skill_store.py`
- Create: `backend/tests/test_skill_build_pipeline.py`

Store mutable Draft content in filesystem directories and metadata in the
existing Workbench SQLite database. Publish ZIP, unpacked copy, IR, validation,
reviews, and digest manifest atomically. Do not add an object-store class.

### Task 6: Review records and patch decisions

**Files:**

- Create: `backend/app/services/skill_review.py`
- Create: `backend/tests/test_skill_review.py`

Test seeded semantic contradictions, incremental/full scope, patch proposal,
explicit apply/reject, and release audit retention. No review operation mutates
a Draft or publishes a Version implicitly.

### Task 7: Skill APIs

**Files:**

- Create: `backend/app/api/skills.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_skills_api.py`

Add project/draft/build/review/release/import/read APIs with exact 4xx behavior.
The main Agent performs the shared `main.py` registration to avoid ownership
conflicts.

### Task 8: Task binding migration

**Files:**

- Modify: `backend/app/services/workbench_task_store.py`
- Modify: `backend/app/api/workbench_v2_tasks.py`
- Modify: `backend/tests/test_workbench_task_store.py`

Replace Workflow binding with Skill Version/digest. Because the product is not
deployed, use one explicit destructive schema migration with backup and tests;
do not retain dual Workflow/Skill write paths or a binding table.

### Task 9: Frozen invocation and runtime adapter

**Files:**

- Create: `backend/app/services/skill_run_invocation.py`
- Create: `backend/app/services/skill_run_executor.py`
- Modify: `backend/app/services/workbench_task_run.py`
- Modify: `backend/app/services/workbench_workflow_runner.py`
- Create: `backend/tests/test_skill_run_invocation.py`
- Create: `backend/tests/test_skill_run_executor.py`

Freeze invocation before execution and translate it through the existing
Harness. The main Agent owns modifications to runner hot files. First use a fake
runtime, then company CodeAgent. Add Claude Code and OpenCode adapters only after
the common contract passes.

### Task 10: Judge and delivery

**Files:**

- Create: `backend/app/services/skill_judge.py`
- Modify: `backend/app/services/workbench_deliverables.py`
- Create: `backend/tests/test_skill_judge.py`
- Modify: `backend/tests/test_workbench_deliverables.py`

Prove session isolation, input/artifact scope, `PENDING_VALIDATION -> READY`, and
full-execution/selective-delivery behavior. A missing optional Judge warns; a
missing Skill-required Judge prevents READY.

### Task 11: Skill-first product UI

**Files:**

- Create: `frontend/src/features/skills/`
- Modify: `frontend/src/features/tasks/task-wizard.tsx`
- Modify: `frontend/src/features/runs/run-cockpit-page.tsx`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/types.ts`
- Create: `frontend/e2e/skill-first-task-run.spec.ts`

Pass a UI Design Gate before implementation. Render project/version/review,
Task inputs/deliveries, current step, next action, capability/Judge state, and
artifact/delivery distinction on existing product surfaces.

### Task 12: Legacy removal and final evidence

**Files:**

- Delete: `frontend/src/features/workflows/`
- Delete/modify: Workflow-only API/store/preset modules after call-site audit
- Create: `docs/reports/f014-skill-first-acceptance.md`
- Create: `docs/review-notes/f014-*.md`

Run source and route gates before deletion, remove only product-specific
Workflow code, and retain generic scheduler/checkpoint/event/artifact machinery.
Map every AC to a command, log, artifact, screenshot, or independent verdict.

## 5. Sub-Agent Plan

Maximum concurrency is three child Agents plus the main integrator. A child may
own one production path in a wave. Shared integration files are never assigned
to multiple Agents.

### Wave 0: Contract discovery

| Agent | Ownership | Output |
|---|---|---|
| archive-contract | read-only ZIP analysis and fixture tests | 37-file trace map and semantic golden summary |
| schema-contract | schemas and schema fixtures only | six schemas with red/green evidence |
| runtime-seam | read-only existing Harness/Attempt audit | exact reuse/adapt/remove matrix |

Main Agent resolves contract disagreements and owns the terminal schema. No
production runtime edits occur in this wave.

### Wave 1: Skill domain

| Agent | Ownership | Output |
|---|---|---|
| importer | `skill_package_importer.py`, path helper, importer tests | safe Pack/Skill import |
| compiler | validator/compiler modules and tests | deterministic IR and diagnostics |
| store-review | store/build/review modules and tests | immutable releases and explicit patch decisions |

Main Agent owns database migration, API registration, and cross-module types.

### Wave 2: Vertical runtime

| Agent | Ownership | Output |
|---|---|---|
| invocation | new invocation/executor modules and tests | frozen runtime bridge |
| judge-delivery | Judge and delivery modules/tests | isolated validation and filtering |
| frontend | new Skill UI and isolated component/E2E tests | Skill-first user journey |

Main Agent alone modifies `workbench_task_store.py`, `workbench_task_run.py`,
`workbench_workflow_runner.py`, `main.py`, and shared frontend API/types after
receiving five-part handoffs.

### Wave 3: Acceptance

| Agent | Role | Constraint |
|---|---|---|
| regression | run backend/frontend/Playwright and restart/cancel matrix | no production edits |
| runtime reviewer | adversarial contract, isolation, recovery review | did not author reviewed code |
| Vision Guardian | compare original decisions and ZIP semantics to real UX | distinct from author and reviewer |

Any review fix returns to the owning implementation Agent, then to the same
reviewer. The main Agent cannot approve its own integration changes.

Every handoff includes What, Why, Tradeoff, Open Questions, and Next Action plus
the exact red/green commands and changed paths.

## 6. Required Commands and Evidence

Initial focused gates:

```bash
cd backend
python -m pytest -q tests/test_skill_schemas.py tests/test_skill_package_importer.py
python -m pytest -q tests/test_skill_package_validator.py tests/test_skill_ir_compiler.py
python -m pytest -q tests/test_skill_store.py tests/test_skill_build_pipeline.py tests/test_skill_review.py
python -m pytest -q tests/test_skill_run_invocation.py tests/test_skill_run_executor.py tests/test_skill_judge.py
```

Integration gates:

```bash
cd backend
python -m pytest -q tests/test_workbench_task_store.py tests/test_workbench_task_run.py tests/test_workbench_deliverables.py
python -m pytest -q
cd ../frontend
npm run lint
npm run build
npx playwright test e2e/skill-first-task-run.spec.ts --project=chromium
```

Evidence is incomplete unless it records command, final SHA, main sync state,
exit code, test counts, relevant artifact paths, screenshots, and the independent
review verdict.

## 7. Stop Conditions

Stop before implementation when the company CodeAgent contract cannot be
observed, the local fixture cannot represent the chosen acceptance scenario,
or the UI Design Gate is not approved. Ordinary red tests, implementation
failures, and expected archive mutations are not blockers.
