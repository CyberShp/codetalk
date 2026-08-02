---
topics: [workflow, artifacts, customization, output-contracts]
doc_kind: design
created: 2026-08-01
---

# Output Artifact Customization

## Goal

One local user can define what different features and workflows must produce
without changing CodeTalk source code. A workflow should resolve an
output contract before execution, pass that contract to the Agent, validate the
returned artifacts, and render/download the outputs in the expected shape.

## Problem

Hard-coded outputs are too rigid:

- one feature owner may want a Markdown SFMEA table with custom risk levels;
- another may require a CSV matrix for import into an internal test platform;
- a safety-critical feature may require evidence links for every row;
- a reviewer may require a short executive summary plus a machine-readable JSON
  manifest;
- the same workflow may need different terminology or section order depending
  on the product line.

The customization target is the output artifact contract, not just visual
styling.

## Core Model

### Artifact Profile

An artifact profile is a versioned bundle of output definitions.

```json
{
  "id": "storage-feature-default",
  "version": 1,
  "scope": {
    "workspace_ids": ["spdk"],
    "feature_tags": ["storage", "iscsi"]
  },
  "artifacts": [
    {
      "id": "test_design",
      "filename": "test_design.md",
      "format": "markdown",
      "required": true,
      "schema_id": "blackbox-test-design-v2",
      "renderer": "markdown-table",
      "instructions": "Include external trigger, expected observation, and evidence for every case."
    }
  ]
}
```

### Artifact Definition

Each artifact definition answers:

- what file must exist;
- which format it uses: Markdown, JSON, CSV, XLSX later;
- whether it is required;
- which schema validates it;
- which renderer or template presents it;
- which examples and terminology should guide the Agent;
- which close-gate rules decide accepted/rejected.

### Resolution Order

When a workflow starts, CodeTalk resolves one effective output contract:

1. Run-specific profile selected by the user.
2. Workspace binding selected for this local workspace.
3. Feature profile matched by explicit feature tags on the run.
4. User default profile.
5. Built-in workflow default.

The MVP resolves exactly one profile; it does not merge profile artifact lists.
Feature tags are explicit ordered run metadata, and the first tag with a local
binding wins. A workspace may have one default binding and each feature tag may
have one binding. A selected run profile always wins. An ambiguous or missing
binding falls through to the next level instead of using edit time as a tie
breaker.

Artifact ids and normalized filenames must both be unique inside a profile.
Profiles cannot weaken global safety rules such as evidence validation, path
validation, or required envelope/manifest generation.

## UX Direction

### Profile Library

Add a settings/workbench view for output profiles:

- profile list with scope, version, and last edited time;
- copy from built-in profile;
- edit artifact list;
- edit Markdown/CSV/JSON schema fields using forms first and raw JSON as
  advanced mode;
- attach examples of accepted outputs;
- run a sample validation against an existing workflow result.

### Workflow Binding

Workflow designer should expose:

- default output profile;
- allowed profile overrides;
- required artifact summary;
- warnings when selected inputs do not satisfy a profile requirement.

### Run Cockpit

Before execution:

- show resolved output contract;
- show required outputs and validation rules;
- allow choosing a profile when multiple profiles match.

After execution:

- show accepted/rejected artifacts;
- show schema errors in user-facing language;
- allow downloading the profile-shaped bundle.

## Execution Contract

CodeTalk injects the resolved output contract into the task bundle:

```json
{
  "output_contract": {
    "profile_id": "storage-feature-default",
    "profile_version": 1,
    "required_artifacts": [
      {
        "id": "test_design",
        "filename": "test_design.md",
        "format": "markdown",
        "schema_id": "blackbox-test-design-v2"
      }
    ]
  }
}
```

The Agent must produce files matching the contract. CodeTalk validates the files
locally before materializing them as deliverables.

## Validation Rules

Validation has three levels:

1. File contract: expected filename, format, non-empty content, manifest entry.
2. Schema contract: required fields, table columns, JSON schema, CSV columns.
3. Evidence contract: source paths, line ranges, workspace-local files, hashes,
   accepted evidence ids, and no unverifiable claims marked as verified.

Invalid artifacts stay downloadable as diagnostics but do not enter Evidence
Memory or final accepted reports.

## Storage

Suggested backend entities:

- `artifact_profiles`
  - `id`
  - `name`
  - `version`
  - `scope_json`
  - `artifacts_json`
  - `created_at`
  - `updated_at`
- `workflow_profile_bindings`
  - `workflow_id`
  - `profile_id`
  - `allow_user_override`
- `task_run_output_contracts`
  - `run_id`
  - `resolved_profile_json`
  - `resolved_at`

Keep profiles in SQLite first. Export/import JSON later for portability between
local installations. CodeTalk does not add users, roles, approvals, or team
ownership for this feature.

## MVP

1. Define artifact profile schemas in backend.
2. Add built-in profiles for the core test workflows.
3. Let workflow definitions reference a profile id.
4. Resolve profile at task-run creation and write `output_contract.json`.
5. Inject the contract into Agent task bundles.
6. Validate required artifacts and produce `artifact_validation.json`.
7. Render accepted artifacts in the run cockpit.

## Not In MVP

- full WYSIWYG report designer;
- arbitrary plugin execution inside renderers;
- weakening evidence validation per user;
- online marketplace for profiles;
- users, roles, approvals, or team ownership;

## Open Questions

- Which internal formats need first-class CSV/XLSX export in addition to
  Markdown/JSON?
- Which first-class feature tags should ship as optional examples? Tags remain
  explicit run metadata in the MVP.
