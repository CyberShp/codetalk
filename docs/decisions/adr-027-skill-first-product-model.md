---
feature_ids: [F014]
topics: [adr, skill-first, product-model, task, run-attempt]
doc_kind: architecture-decision-record
created: 2026-08-04
status: accepted
---

# ADR-027: Skill-first Product Model

## Context

The current product exposes Workflow, Workflow Version, presets, and a canvas
as first-class concepts.  The F014 user journey is instead to select a complete
analysis method, bind its inputs and an Agent runtime, execute it, and inspect
trustworthy artifacts.  The supplied Codetalks v2.4 archive contains five
separate scenarios; treating the archive as one editable graph would lose that
product meaning and recreate the legacy Workflow model under a new label.

F014 is based directly on `main`.  F012 and F013 are not branch bases,
implementation dependencies, or sources for this decision.

## Decision

The canonical durable product chain is:

`Skill Project -> Skill Version -> Task -> Run Attempt`.

- A **Skill** represents exactly one analysis scenario and its complete
  methodology.  V1 does not support user-authored dynamic DAGs, multi-Skill
  orchestration, or runtime step pruning.
- A **Skill Pack** is an optional one-to-many import and organization
  container for independent Skill Projects.  A source archive containing
  multiple scenarios becomes multiple Skills in one Pack.  It is not part of
  the canonical execution chain or a multi-scenario Skill.
- A **Skill Project** owns mutable Draft filesystem content.  It is the
  authoring identity, not executable historical truth.
- A **Skill Version** is an explicitly published immutable release of one
  Project build.  It is the only Skill reference that a Task may bind.
- A **Task** binds exactly one Skill Version and records user-selected inputs,
  Agent runtime and model, the requested output-token ceiling, queue/Agent/
  script/validation/overall timeout choices, and selected deliveries.  It has
  no parallel Workflow or Workflow Version binding.
- A **Run Attempt** is one durable execution of the Task.  It freezes the
  selected Skill Version and its content digest, invocation inputs, runtime
  capability/preflight evidence, and delivery selection before work begins.

V1 always executes the complete Skill.  Selected deliveries filter
presentation and download packaging only; they never omit upstream steps or
make unselected declared outputs unavailable to the internal artifact ledger.

## Consequences

- UI, APIs, and persistence must speak in Pack, Project, Version, Task, and
  Attempt terms.  A version selector replaces a Workflow selector in Task
  creation.
- The imported official archive must become five Skills, preserving its source
  methodology rather than merging it into a generic canvas.
- Historical reproducibility comes from a frozen Version and Attempt, not from
  a mutable Project Draft or current settings.
- Product-specific Workflow routes, canvas, presets, version bindings, and
  hard-coded professional-analysis prompts are removed only after the
  Skill-first vertical path is accepted.  Generic Task, event, checkpoint,
  artifact, and cockpit capabilities remain reusable runtime assets.

## Non-Goals

- User-authored dynamic DAGs, multi-Skill orchestration, and runtime step
  pruning in V1.
- Keeping a dual Workflow/Skill binding or turning a Pack into an executable
  aggregate.

## Alternatives Considered

- Retain Workflow/Workflow Version as aliases: rejected because it preserves a
  second binding truth source and legacy product semantics.
- Make a Pack the executable unit: rejected because it obscures the one-scenario
  Skill contract and independent release history.

## Affected Scope

Task creation and persistence move from Workflow Version to Skill Version.
Import, authoring, release, task, cockpit, and delivery surfaces adopt the
Pack/Project/Version/Task/Attempt vocabulary; generic runtime assets remain.

## Rollback

Before legacy removal, disable the Skill-first entry points and restore the
previous `main` product routes and schema from the migration backup.  Do not
translate or mutate published Skill Versions or frozen Run Attempts.

## Validation

Acceptance requires five independent scenario Skills from the pinned archive,
one-Version-per-Task binding, frozen Attempt evidence, and a delivery-filter
test proving that a selected subset does not change full Skill execution.
