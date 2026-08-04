---
feature_ids: [F014]
related_features: [F002, F004, F005, F006, F008, F010]
topics: [skill-first, skill-package, agent-runtime, task-run, artifact, judge]
doc_kind: spec
created: 2026-08-04
---

# F014: Skill-first Product and Runtime

> **Status**: spec | **Owner**: Codex | **Priority**: P0

## Why

CodeTalk currently exposes Workflow, Workflow Version, presets, and a canvas as
product concepts even though the durable user intent is to select a complete
analysis method, bind inputs and an Agent, run it, and receive trustworthy
deliverables. The product model must become:

`Skill Project -> Skill Version -> Task -> Run Attempt`.

The first authoritative migration source is
`codetalks-fused-v2.4-zh.zip` with SHA-256
`7369ef35d339bc554610754ceb385b78d15f94fc8e1e5435350c4ebcf2b27325`.
It contains five scenarios, nine ordered steps, three mandatory core rules,
37 required artifacts, eight final outputs, a deterministic `run_guard.py`,
and an independent Judge contract. The refactor is accepted only if those
semantics survive migration and become observable product behavior.

F012 and F013 are explicitly excluded as branch bases and implementation
dependencies. F014 starts from `main` and reuses only capabilities already on
`main`.

## Product Decisions

1. One Skill represents one analysis scenario; a multi-scenario archive imports
   as a Skill Pack containing independent Skills.
2. V1 always executes the complete Skill. Delivery selection filters only what
   is presented or downloaded.
3. Draft files are mutable on the local filesystem; released Skill Versions are
   immutable ZIP artifacts with stable digests.
4. Deterministic structural errors block release. AI review produces findings
   and optional patches but never silently edits or publishes.
5. Judge is a Skill-level capability, not a platform-wide requirement. A Skill
   may declare it optional or required.
6. Producer and Judge use isolated sessions; Judge never consumes Producer
   conversation history.
7. Custom scripts are allowed with bounded working directory, timeout, exit
   code, stdout/stderr capture, and artifact-path enforcement.
8. The old Workflow product path is deleted only after the Skill-first vertical
   path is proven end to end.

## What

### Phase A: Contracts and deterministic build

Define the six V1 schemas, safe ZIP import, Skill Pack splitting, deterministic
validation, Skill IR compilation, deterministic candidate ZIP, and reproducible
content digest. Preserve UTF-8 filenames and account for every source archive
entry.

### Phase B: Skill domain and review

Add Skill Project, Draft, Version, Build, Review, and Pack metadata; local draft
and release storage; filesystem rescan; full and incremental AI review; explicit
human patch decisions; review-gated immutable publication; and read APIs. Do not
add a placeholder object-storage implementation before a second backend exists.

### Phase C: Task and Run integration

Replace Task's Workflow Version binding with a frozen Skill Version and digest.
Create a versioned Skill Run Invocation carrying input snapshot, selected
deliveries, Agent runtime, capability report, session identity, and artifact
root. Reuse existing Attempt, checkpoint, event, cancellation, recovery,
artifact authority, and cockpit mechanisms.

### Phase D: Official Codetalks pack and independent Judge

Import the supplied archive as a five-Skill Pack. Publish
`codetalks-module-full-analysis` as the primary acceptance Skill without
rewriting its methodology. Its depth mode executes all nine steps and requires
an independent Judge before `READY`.

### Phase E: Product replacement and legacy removal

Make Task creation and Run Cockpit Skill-first, expose truthful execution,
quality, and delivery state, then remove Workflow Center, Designer, Workflow
Version, presets, Workflow-to-Task binding, hard-coded Workbench skill prompts,
and the old staged professional-analysis entry path.

## Acceptance Criteria

### Phase A (Contracts and deterministic build)

- [ ] AC-A1: Six versioned JSON Schemas have positive and adversarial fixtures and reject unknown or ambiguous terminal fields.
- [ ] AC-A2: Import of the pinned archive accounts for all 37 files, preserves the three UTF-8 template names, rejects traversal/symlink escapes, and records the source SHA-256.
- [ ] AC-A3: The five source scenarios become five independent Skills in one Pack; a single multi-scenario Skill is rejected.
- [ ] AC-A4: The module-analysis Skill IR retains nine ordered steps, three core-rule acknowledgements, 37 required artifacts, eight final outputs, script declarations, completion gates, and Judge requirements.
- [ ] AC-A5: Identical source bytes produce identical IR and content digest; missing files, duplicate IDs, broken references, cycles, undeclared producers, and invalid paths fail with exact locations.

### Phase B (Skill domain and review)

- [ ] AC-B1: Draft files can be created, edited externally, rescanned, validated, and built without UI or database state becoming a second content authority.
- [ ] AC-B2: After the required full Review decision, explicit publication creates an immutable Skill Version containing source package, unpacked files, IR, validation, review records, deterministic content digest, separate review evidence digest, and a manifest linking both.
- [ ] AC-B3: AI review detects seeded semantic contradictions and produces findings plus an optional patch without applying or publishing it; actual product LLM provider/model/output/session provenance is retained without credentials.
- [ ] AC-B4: Deterministic errors block release; acknowledged AI high-risk findings remain visible but do not become hidden structural blockers.

### Phase C (Task and Run integration)

- [ ] AC-C1: Task binds exactly one Skill Version and stores its digest, inputs, Agent runtime, selected deliveries, and model/budget configuration without a parallel binding truth source.
- [ ] AC-C2: Run Attempt freezes Skill ZIP/IR/digest, input snapshot, capability report, invocation, Agent session, Judge configuration, and selected deliveries before execution.
- [ ] AC-C3: Selecting one delivery does not skip upstream Skill steps; unselected outputs remain internal artifacts and never enter the delivery package.
- [ ] AC-C4: Run Attempt, Agent Session, and Agent Process are separate persisted concepts; a disposable process cannot erase a resumable Session or completed checkpoint.
- [ ] AC-C5: Lifecycle events persist in order from capability discovery and preflight through session creation, start, messages/tools/artifacts, waiting/resume, and one terminal state.
- [ ] AC-C6: Killing the Agent process preserves committed checkpoints and artifacts, discards uncommitted temporary output, and resumes from the last valid checkpoint without repeating completed steps.
- [ ] AC-C7: Restarting CodeTalk reconciles unfinished Runs and Sessions deterministically and resumes from the frozen Skill Version rather than a mutable Draft.
- [ ] AC-C8: A missing, incompatible, or corrupt Agent Session is invalidated once with a recorded reason; recovery creates one clean Session or fails explicitly without an infinite retry loop.
- [ ] AC-C9: Cancellation is idempotent, terminates child processes, prevents later artifact/Judge/completed transitions, and leaves no nonterminal Run; queue, Agent, script, validation, and overall timeouts remain distinguishable.
- [ ] AC-C10: Company CodeAgent, Claude Code, and OpenCode expose the same lifecycle contract; unsupported resume, tool, or cancellation capabilities are explicit in the capability report and never silently ignored.
- [ ] AC-C11: The professional Skill path does not call legacy `ai_staged_execution` or infer methodology from target text.

### Phase D (Official pack and Judge)

- [ ] AC-D1: The supplied archive imports into a Pack and publishes `codetalks-module-full-analysis` without losing source files or silently rewriting instructions.
- [ ] AC-D2: A complete depth run creates the three sibling run directories, all declared intermediate artifacts, and all eight formal Markdown outputs.
- [ ] AC-D3: Producer completion without Judge yields `PENDING_VALIDATION`; only an isolated Judge session with recorded checked artifacts can yield `READY`.
- [ ] AC-D4: Judge receives frozen inputs, source snapshot, artifacts, and Skill contract but no Producer conversation transcript.
- [ ] AC-D5: A local CI fixture proves the vertical path without depending on intranet source repositories or credentials.
- [ ] AC-D6: Final real-provider acceptance runs the complete CodeTalk vertical and a bounded Clowder AI runtime comparison through OpenCode with the official DeepSeek-compatible route and `deepseek/deepseek-v4-flash`; actual AI Review/product LLM calls use `deepseek-v4-flash`; every F014 acceptance Agent invocation records a declared 200,000-token context capacity and requests at most 4,096 output tokens without persisting credentials.

### Phase E (Product replacement and legacy removal)

- [ ] AC-E1: Task creation selects Skill Version and Agent Runtime, renders inputs and deliveries from Skill IR, and contains no Workflow selection.
- [ ] AC-E2: Run Cockpit exposes current Skill step, next action, capability degradation, Judge status, execution/quality/delivery state, and artifacts on existing surfaces.
- [ ] AC-E3: Workflow product routes, canvas, versions, presets, Task binding, hard-coded Workbench skills, and old professional staged entry are absent after the vertical gate passes.
- [ ] AC-E4: Backend, frontend, restart/cancel integration, Playwright, and the fixed CodeTalk/Clowder AI OpenCode + DeepSeek V4 Flash acceptance suites pass on the final SHA.
- [ ] AC-E5: Desktop screenshots at 1440x900 and 1280x800 and mobile at 390x844 show no overlap, truncation, dead Workflow navigation, or misleading state.
- [ ] AC-E6: Quality gate, independent review, receive-review remediation, Vision Guardian, and merge gate complete with no self-review.

## Acceptance Method

| Layer | Method | Required evidence |
|---|---|---|
| Archive | Integrity, safe-path, filename, and inventory tests | pinned SHA, 37/37 accounting, UTF-8 path manifest |
| Contract | Schema positive/negative fixtures and IR golden tests | exact validation paths and stable golden digest |
| Component | Store/build/review TDD suites | immutable release, rescan, patch non-application evidence |
| Agent lifecycle | Fake Agent plus real-runtime create/start/event/kill/restart/session-loss/cancel/timeout matrix | frozen invocation, ordered events, checkpoint replay, process cleanup, one terminal state |
| CodeTalk vertical | Real OpenCode + DeepSeek V4 Flash full Skill run plus actual product LLM review | runtime/model receipts, nine steps, 37 artifacts, eight outputs, delivery filter |
| Clowder comparison | Real OpenCode + DeepSeek V4 Flash bounded runtime run on the same local fixture | session, source/tool event, final response, runtime/model/limit receipt |
| Judge | Separate-session adversarial acceptance | no transcript leakage, `PENDING_VALIDATION -> READY` evidence |
| Product | Playwright workflows and screenshots | Task, Cockpit, Pack/Skill pages at required viewports |
| Removal | Source/API/route search gates and regression suite | zero live Workflow product references, final full-suite log |

## Dependencies

- **Evolved from**: F002 (main already contains the provider/harness boundary to adapt)
- **Blocked by**: none; F012 and F013 are intentionally excluded
- **Related**: F004, F005, F006, F008, F010 (their reusable Task, Run, cockpit, artifact, and profile assets are migrated)

## Risk

| Risk | Mitigation |
|---|---|
| Recreating Workflow under a new name | One-scenario Skill rule; no user-authored DAG or dynamic pruning in V1 |
| Losing methodology during import | 37/37 inventory plus semantic mutation tests and source-to-IR trace map |
| Runtime rewrite expands scope | Thin Skill Invocation adapter over main's existing Harness and Attempt lifecycle |
| Judge becomes universal policy | Capability is optional platform-wide and required only when declared by a Skill |
| UTF-8 ZIP names are corrupted | Use UTF-8-aware archive parser and compare normalized paths plus content hashes |
| Old and new product paths diverge | No compatibility migration; delete Workflow only after one complete vertical run |
| Real-provider profile silently drifts | Freeze runtime, provider, model, declared 200K context capacity, requested 4096 max output, CLI version, and credential readiness in every acceptance record |

## Non-Goals

- Dynamic step pruning or multi-Skill DAG execution.
- F012 benchmark corpus, gold truth, three-axis evaluator, or baseline freezer.
- F013 lifecycle changes or branch history.
- Skill marketplace, multi-user permissions, production migration, or complex sandbox.
- Object storage implementation before a real second backend is selected.

## Open Questions

| # | Question | Status |
|---|---|---|
| OQ-1 | Exact company CodeAgent invocation and capability discovery contract | Resolve before Phase C |
| OQ-2 | Final local CI fixture domain and source snapshot | Resolve before Phase D |

## Key Decisions

| # | Decision | Reason | Date |
|---|---|---|---|
| KD-1 | Start from main and exclude F012/F013 | Avoid importing blocked quality work and high-conflict runtime changes | 2026-08-04 |
| KD-2 | Import the supplied ZIP as a five-Skill Pack | The archive contains five distinct scenarios while one Skill must represent one scenario | 2026-08-04 |
| KD-3 | Keep source-declared required Judge semantics | Platform optionality must not weaken the official Skill's READY contract | 2026-08-04 |
| KD-4 | Reuse Attempt/checkpoint/event/artifact mechanisms | These are durable final-system assets; rewriting them is a detour | 2026-08-04 |
| KD-5 | Test inside every Task and at every Phase gate | Waiting until final integration would hide ownership and lifecycle defects | 2026-08-04 |
| KD-6 | Development/test Agents use GPT-5.6 Terra medium; main and independent audit use GPT-5.6 Sol high | Keep implementation throughput separate from integration and audit judgment while using models available to the current control plane | 2026-08-04 |
| KD-7 | Final real-provider profile is OpenCode + DeepSeek V4 Flash for the full CodeTalk run and bounded Clowder runtime comparison, with actual DeepSeek V4 Flash product LLM work | Match the intended intranet deployment model without requiring Clowder AI to implement CodeTalk's Skill product layer | 2026-08-04 |

## Timeline

| Date | Event |
|---|---|
| 2026-08-04 | Created independent main-based branch and pinned acceptance archive |

## Review Gate

- Task 2 schemas and ADRs require independent `gpt-5.6-sol` high approval before Task 3 importer implementation begins.
- Contract and architecture changes require an independent reviewer before runtime integration.
- Authors may test their slices but may not approve them.
- A separate Vision Guardian verifies the original product decisions and ZIP semantic preservation.

## Requirement Checklist

| ID | Requirement | AC | Verification | Status |
|---|---|---|---|---|
| R1 | Replace the Workflow product model with Skill-first | AC-C1, AC-E1, AC-E3 | API, Playwright, source gate | [ ] |
| R2 | Preserve and migrate the supplied v2.4 package | AC-A2-A4, AC-D1-D2 | inventory, IR golden, vertical run | [ ] |
| R3 | Define concrete acceptance methods and standards | all ACs | evidence matrix and final report | [ ] |
| R4 | Plan bounded sub-Agent execution | AC-E6 | ownership log and independent reviews | [ ] |
| R5 | Agent lifecycle must survive process death, service restart, session loss, cancellation, and timeout | AC-C4-C10 | fake/real lifecycle matrix and persisted evidence | [ ] |
| R6 | Acceptance uses the fixed OpenCode/DeepSeek profile and intranet-sized limits | AC-D6, AC-E4 | preflight probe, frozen invocation, CodeTalk/Clowder/LLM evidence | [ ] |

### Coverage Check

- [x] Every current requirement maps to at least one AC.
- [x] Every AC has an executable evidence class.
- [ ] Frontend requirement-to-screenshot mapping will be finalized at the UI Design Gate.

## Links

| Type | Path | Description |
|---|---|---|
| Plan | `docs/plans/2026-08-04-f014-skill-first-runtime.md` | TDD sequence, acceptance matrix, and sub-Agent waves |
| Source | `codetalks-fused-v2.4-zh.zip` | User-supplied acceptance archive, pinned by SHA-256 |
