---
feature_ids: [F014]
topics: [quality-gate, skill-validator, skill-ir-compiler, task-4]
doc_kind: quality-gate-report
created: 2026-08-05
---

# F014 Task 4 Validator And IR Compiler Quality Gate

## Scope

This gate covers Task 4 only: deterministic `codetalk-skill-v1`
cross-reference validation, topological ordering, source-file existence checks,
and deterministic `skill-ir-v1` compilation for both generic Skill documents
and the Codetalks v2.4 manifest shape. It does not create mutable Draft storage,
immutable Skill Versions, review records, publication, Task binding, runtime
execution, or frontend surfaces.

## Vision And Contract Check

The implementation preserves these F014 boundaries:

- schemas remain syntax/shape contracts; Task 4 owns cross-document references,
  producer/consumer integrity, cycles, path canonical collisions, and source
  file existence;
- compiler refuses invalid input and does not repair or infer missing
  references;
- generic Skill IR is schema-valid `skill-ir-v1` and omits source-only fields
  such as `name`;
- official Codetalks v2.4 compilation reads explicit
  `workflow-manifest.json` plus `workflows/<scenario>.md`; it does not infer
  scenarios from prose;
- Task 5 remains responsible for filesystem Draft authority, deterministic
  build ZIPs, immutable release storage, review evidence digests, and locking.

## Coverage

| Boundary | Evidence | Result |
|---|---|---|
| IDs and references | duplicate step IDs, missing dependencies, missing script, missing delivery/Judge/log artifacts, missing artifact producer | Pass |
| Producers and completion gates | artifact producer must exist, step `produces` references must name declared artifacts, each artifact has only one producing step, and step completion gates must require artifacts produced by that step | Pass |
| Dependency graph | cycle detection returns exact `steps` location; valid graph emits deterministic topological order | Pass |
| Paths | unsafe source paths, source symlink escapes, unsafe referenced paths, source file existence, artifact output path ambiguity, and NFC+casefold path collisions are rejected with exact locations | Pass |
| Compile gating | invalid documents, invalid JSON, malformed v2.4 manifests, and document/source-byte mismatches raise `SkillPackageValidationError` before IR is produced | Pass |
| Determinism | identical source bytes produce identical IR and content digest; source digest rows cover every regular source file in deterministic lexical order | Pass |
| Schema integration | generic and Codetalks v2.4 IR outputs validate against `skill-ir-v1` | Pass |
| Delivery integrity | delivery packages can reference only delivery-visible artifacts, and every delivery-visible artifact must be consumed by a delivery | Pass |
| Codetalks v2.4 semantics | module-analysis IR retains nine ordered steps, three core-rule acknowledgements, 37 required artifacts, eight formal deliveries, `run_guard.py` with its own run-state log artifact, selected workflow binding, manifest guard fields, Step 04 flow gate fields, and required/optional Judge semantics by scenario | Pass |

## Red-Green Evidence

Initial Task 4 RED:

```text
PYTHONPATH=backend uv run --python 3.12 \
  --with-requirements backend/requirements.txt pytest -q \
  backend/tests/test_skill_package_validator.py \
  backend/tests/test_skill_ir_compiler.py
=> 16 failed
```

The failures were expected `ModuleNotFoundError` failures for the two missing
Task 4 modules.

During implementation, schema-shaped IR tests exposed two confirmed findings:

| Finding | Red evidence | Green evidence |
|---|---|---|
| Generic IR leaked source-only `name` field | `skill-ir-v1` schema rejected compiler output with unexpected `name` | compiler now drops `name` before hashing and returning IR |
| Codetalks v2.4 generated schema-invalid semantic IDs | `step.01` and numeric/Unicode-derived artifact IDs failed `semanticId` | compiler now emits `step.step-01` style IDs and stable `artifact.required-<digest-prefix>` IDs |
| Step outputs could name undeclared artifacts | `steps[1].produces[0] = artifact.missing` reported a secondary completion-gate mismatch instead of the root output error | validator now reports `unknown_produced_artifact` at `steps[1].produces[0]` before gate checks |
| Codetalks v2.4 digest omitted unreferenced source files | modifying `references/tool-routing.md` did not change the compiled content digest | v2.4 source file digests now include every regular file under the source root |
| Generic Skill digest omitted unreferenced source files | modifying an extra source file did not change generic compiled content digest | generic source file digests now include every regular file under the source root |

Independent review returned `CHANGES_REQUESTED` on 2026-08-05. The confirmed
findings were fixed with red tests before this gate was refreshed:

| Review finding | Red evidence | Green evidence |
|---|---|---|
| Codetalks v2.4 bypassed validation and could return schema-invalid IR | duplicate manifest step IDs and `01/invalid` step IDs compiled without an error | v2.4 compilation now constructs a `codetalk-skill-v1` document, runs `validate_skill_document`, validates final `skill-ir-v1`, and returns exact errors |
| Generic compilation was not bound to declared source bytes | changing only the in-memory document title changed IR/digest while `skill.json` bytes were unchanged | compiler now rejects document/source mismatch with `source_document_mismatch` at `source_path` |
| Artifact producer validation allowed multiple producers | adding `artifact.raw` to a second step's `produces` list returned `ok=True` | validator now returns `multiple_artifact_producers` at the second producer location |
| `run_guard` log referenced the Judge-state artifact | script logs pointed at `artifact.internal-run-state` while that ID represented `内部索引/独立审查状态.json` | `内部索引/运行状态.json` now owns `artifact.internal-run-state`; `内部索引/独立审查状态.json` owns `artifact.internal-judge-state` |
| Required verification gate was stale/red | review reproduced a red full run against stale expectations | fresh gates below are green after updating deterministic source digest expectations |
| Unsafe `source_path` could generate schema-invalid IR | a POSIX file named with a backslash compiled into invalid `source_file_digests` | validator now rejects unsafe `source_path` before missing-source checks |

Second independent review also returned `CHANGES_REQUESTED` on 2026-08-05. The
confirmed findings were fixed with red tests before this gate was refreshed:

| Review finding | Red evidence | Green evidence |
|---|---|---|
| Source-root containment was bypassable through symlinks | symlinked `skill.json` and instruction files outside `source_root` validated successfully | validator and compiler now reject symlink source paths and resolved-root escapes |
| v2.4 manifest fields were dereferenced before validation | deleting `steps[0].markdown_min_chars` raised raw `KeyError` | v2.4 manifest shape is checked first and returns `missing_manifest_field` at `workflow-manifest.json.steps[0].markdown_min_chars` |
| Delivery/internal artifact integrity was not enforced | a delivery could reference internal `artifact.raw`, leaving delivery-visible `artifact.report` unconsumed | validator now rejects `delivery_artifact_not_visible` and `unconsumed_delivery_artifact` |
| Scenario workflow was not bound into terminal IR | all five scenario IRs were identical after removing `skill_id` and `content_digest` | selected `workflows/<scenario>.md` is now present in the terminal input label, first step `instruction_path`, and step titles, and differentiates scenario IR |
| Artifact output paths were ambiguous | duplicate artifact paths and `out` vs `out/report.md` validated | validator now rejects `duplicate_artifact_path` and `artifact_path_prefix_conflict` |

Third independent review also returned `CHANGES_REQUESTED` on 2026-08-05. The
confirmed findings were fixed with red tests before this gate was refreshed:

| Review finding | Red evidence | Green evidence |
|---|---|---|
| v2.4 manifest fields consumed by `run_guard.py` were neither validated nor compiled into IR | deleting `evidence_allowed_status`, `coverage_allowed_outcomes`, `flow_required_headings`, or `flow_key_narrative_headings` still compiled | manifest shape validation now rejects missing guard fields, and compiled gates retain these values |
| Step 04 flow-card validation fields were dropped | deleting `requires_glob` or `flow_narrative_validation` from Step 04 still compiled | Step 04 now rejects missing flow fields and carries `requires_glob`, `flow_narrative_validation`, and heading requirements in `completion_gate` |
| Scenario-specific workflow semantics were display-only | selected workflow appeared only in labels/titles and `issue-regression` lacked MR input | the first step now uses `workflows/<scenario>.md` as its structured `instruction_path`, and `issue-regression` declares required `input.mr-link` |
| Completion gate validation was one-directional | removing a required artifact from its producer gate or putting an optional artifact in a gate still validated | validator now rejects `required_artifact_missing_from_gate` and `optional_artifact_in_gate` |
| Malformed JSON raised raw parser exceptions | invalid `skill.json` or `workflow-manifest.json` raised `JSONDecodeError` | compiler now raises `invalid_json` with stable paths (`source_path` or `workflow-manifest.json`) |

Fourth independent review also returned `CHANGES_REQUESTED` on 2026-08-05. The
confirmed findings were fixed with red tests before this gate was refreshed:

| Review finding | Red evidence | Green evidence |
|---|---|---|
| Step 01 lost its own instruction file | selected workflow replaced `steps/01-intake-and-scope.md` in the first step | first step now keeps `instruction_path=steps/01-intake-and-scope.md`, while `selected_workflow_path` binds the scenario at the IR root |
| Step 04 flow gate fields could be empty/false | `requires_glob=[]` or `flow_narrative_validation=false` still compiled | manifest validation now requires a non-empty glob list and `flow_narrative_validation=true` for Step 04 |
| Judge semantics were hardcoded for every scenario | all five scenario IRs reported `judge.required=true` | `judge.required`/`isolated_session` now follow scenario need, with non-module scenarios optional |
| UTF-8 decode failures escaped raw | invalid bytes in `skill.json` or `workflow-manifest.json` raised `UnicodeDecodeError` | JSON readers now normalize invalid UTF-8 into `invalid_json` validation issues |

Fifth independent review returned `CHANGES_REQUESTED` on 2026-08-05. The
confirmed finding was fixed with red tests before this gate was refreshed:

| Review finding | Red evidence | Green evidence |
|---|---|---|
| Module-analysis artifact cardinality/set could shrink silently | removing one internal required artifact compiled with 36 required artifacts, and removing one formal output compiled with seven deliveries | module-analysis now rejects required-artifact count mismatch and formal-output set mismatch before IR is produced |

Sixth independent review returned `CHANGES_REQUESTED` on 2026-08-05. The
confirmed finding was fixed with red tests before this gate was refreshed:

| Review finding | Red evidence | Green evidence |
|---|---|---|
| New path fields bypassed strict path validation | `selected_workflow_path="./workflow.md"` compiled and `requires_glob=["./out/*.md"]` validated | validator now checks `selected_workflow_path` with strict member-path rules and checks `requires_glob` with a glob-aware strict path validator |

Seventh independent review returned `CHANGES_REQUESTED` on 2026-08-05. The
confirmed finding was fixed with red tests before this gate was refreshed:

| Review finding | Red evidence | Green evidence |
|---|---|---|
| Module-analysis fixed semantic shape could drift while counts stayed green | extra Step 10 compiled, replacing an internal required artifact path compiled with 37 artifacts, and removing a core rule compiled with two core rules | module-analysis now rejects exact step-set, core-rule-set, and full required-artifact-set mismatches |

Eighth independent review returned `CHANGES_REQUESTED` on 2026-08-05. The
confirmed finding was fixed with a red test before this gate was refreshed:

| Review finding | Red evidence | Green evidence |
|---|---|---|
| Module-analysis required artifacts could move between steps while the same 37-path set still passed | swapping `内部索引/独立审查状态.json` from Step 08 with `正式输出/完整分析报告.md` from Step 09 compiled successfully | module-analysis now rejects per-step required-artifact mismatches with `codetalks_required_artifact_step_mismatch` |

## Fresh Verification

Worktree: `/Volumes/Media/codetalk-skill-first-agent-runtime`

Branch/base: `codex/skill-first-agent-runtime`, based on `main@9e1434d9`.

```text
PYTHONPATH=backend uv run --python 3.12 \
  --with-requirements backend/requirements.txt pytest -q \
  backend/tests/test_skill_package_validator.py \
  backend/tests/test_skill_ir_compiler.py
=> 55 passed

PYTHONPATH=backend uv run --python 3.12 \
  --with-requirements backend/requirements.txt pytest -q \
  backend/tests/test_skill_schemas.py \
  backend/tests/test_skill_source_inventory.py \
  backend/tests/test_skill_package_importer.py \
  backend/tests/test_skill_package_validator.py \
  backend/tests/test_skill_ir_compiler.py
=> 307 passed, 2 skipped

CODETALKS_V24_ARCHIVE=/Users/shepard/Downloads/codetalks-fused-v2.4-zh.zip \
  PYTHONPATH=backend uv run --python 3.12 \
  --with-requirements backend/requirements.txt pytest -q \
  backend/tests/test_skill_schemas.py \
  backend/tests/test_skill_source_inventory.py \
  backend/tests/test_skill_package_importer.py \
  backend/tests/test_skill_package_validator.py \
  backend/tests/test_skill_ir_compiler.py
=> 309 passed, 0 skipped
```

Additional checks:

- scoped Python compilation: pass.
- `git diff --check`: pass.
- no frontend or runtime files changed.
- `cat-cafe-skills/` remains absent from the worktree, so the root
  `AGENTS.md` rules are the available local governance source.

## Gate Decision

Task 4 author/integrator self-check is ready for independent review. This
report is not approval and does not authorize Task 5 by itself.
