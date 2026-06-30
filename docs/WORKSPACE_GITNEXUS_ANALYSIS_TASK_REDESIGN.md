---
feature_ids: [F-WORKSPACE-GITNEXUS-ANALYSIS-TASK-REDESIGN]
related_features: []
topics: [workspace, gitnexus, llm, report-generation, test-design]
doc_kind: spec
created: 2026-05-26
---

# Workspace GitNexus Analysis Task Redesign

> Status: spec  
> Owner: TBD  
> Target implementer: AI coding agent / Opus 4.7  
> Scope: CodeTalk workspace report generation after GitNexus indexing

## 0. Read This First

This document is an implementation specification, not a loose PRD. Do not reinterpret the product shape unless a requirement is technically impossible. If an implementation detail conflicts with current code, preserve the behavior stated here and adapt the code around it.

The goal is to stop workspace report generation from expanding a GitNexus index with hundreds or thousands of communities into hundreds or thousands of LLM analysis calls. GitNexus must become a navigation and evidence source. It must not directly define the LLM task fan-out.

## 1. Problem Statement

Current workspace analysis has these observed failures:

1. A large internal repository produced about 1000 GitNexus modules and analysis ran for about 6 hours.
2. The final generated reports contained 0 token / empty content.
3. The report phase reported missing Mermaid-style diagrams, but the real issue was that the LLM output was empty or invalid.
4. Users cannot define the real analysis object. The system implicitly follows GitNexus communities.
5. Absence of the removed Wiki dependency must not block GitNexus-only analysis, but the current user experience makes failures difficult to understand.
6. Internal AI constraints are tight: context is about 192K, output limit is about 8K, and Chinese reports around 2000 tokens may already truncate in practice.

The redesign must optimize for:

- focused analysis;
- deterministic task fan-out;
- report usefulness for test design and SFMEA;
- graceful operation without the removed Wiki dependency;
- robust behavior when the LLM returns empty, truncated, or structurally invalid output.

## 2. Finish Line

After a workspace has been indexed by GitNexus, clicking "Generate Report" opens an analysis task modal. The user defines analysis objects in free text, selects focus directions and report types, optionally edits visible guidance, previews the resolved scope, then starts the task.

For a repository with 1000 GitNexus modules, if the user defines 8 analysis objects, the system must not launch 1000 LLM module analyses. It must resolve the 8 objects into a bounded evidence set and generate selected reports from that evidence.

## 3. Non-Goals

Do not build these in this feature:

- Do not show the raw GitNexus module/community list as a primary selection UI.
- Do not require the removed Wiki dependency for workspace report generation.
- Do not generate security-risk-heavy reports by default.
- Do not allow a user prompt to remove mandatory report quality rules.
- Do not keep the current "one GitNexus community equals one LLM module analysis" behavior.
- Do not mark empty reports as successful.
- Do not solve every code intelligence limitation of GitNexus. GitNexus remains a navigation source, not final truth.

## 4. Current Code Reference

Use these files as the current implementation entry points:

- `frontend/src/app/workspaces/[id]/page.tsx`
  - Current "Generate Report" button calls `api.workspaces.analyze(wsId)` directly.
  - Current module selector is single-select and only affects chat, not report generation.
- `frontend/src/lib/api.ts`
  - Current workspace API wrapper has `workspaces.analyze(wsId)` with no request body.
- `frontend/src/lib/types.ts`
  - Add new workspace analysis request/preview/report-plan types here.
- `backend/app/api/workspaces.py`
  - Current `POST /api/workspaces/{ws_id}/analyze` has no body.
  - Current `GET /api/workspaces/{ws_id}/modules` exposes GitNexus clusters but must not become the new report selection UI.
- `backend/app/adapters/gitnexus.py`
  - Current GitNexus prepare/index adapter.
- `backend/app/services/workspace_pipeline.py`
  - Current `WorkspacePipeline.run(ws_id, repo_path)` creates a shadow task and calls `AnalysisPipeline().run(task_id)`.
- `backend/app/services/analysis_pipeline.py`
  - Current `_phase_module_analysis()` discovers GitNexus communities and may create one LLM task per community.
- `backend/app/services/report_generator.py`
  - Current report generation may write empty streaming output and validates Mermaid/table structure too late.
- `backend/app/llm/base.py`
  - Current streaming collection returns concatenated content without empty-output guard.
- `backend/app/llm/openai_compat.py`
  - Current streaming parser only consumes `choices[0].delta.content`.
- `backend/app/config.py`
  - Existing limits include `analysis_concurrency`, `llm_max_concurrency`, `llm_max_output_tokens`, and `gitnexus_poll_timeout`.

## 5. Product Behavior

### 5.1 Entry Flow

Replace the direct "Generate Report" behavior with:

1. User opens workspace detail page.
2. Workspace must have `indexed === 1`.
3. User clicks "Generate Report".
4. Frontend opens `AnalysisTaskModal`.
5. Modal loads default plan.
6. User edits analysis objects, focus directions, report choices, and optional visible guidance.
7. User clicks "Preview Scope".
8. Backend resolves a bounded scope using GitNexus graph/search and local repo files.
9. Frontend shows a compact scope preview.
10. User clicks "Start Analysis".
11. Backend starts workspace analysis using the submitted plan and resolved scope.

### 5.2 Analysis Object Input

The user does not select raw GitNexus modules. The user defines analysis objects in a text area.

Examples:

```text
iscsi target login path
iscsi target logout / disconnect / session cleanup
iscsi target error handling and retry path
iscsi target long-running session state
FC login and link recovery
FC error handling
```

The system must preserve the user's wording and resolve each object to candidate files/functions/modules through GitNexus and repo search.

### 5.3 Scope Preview

The preview must show:

- number of analysis objects;
- resolved candidate directories;
- resolved candidate files;
- resolved candidate functions/symbols if available;
- related GitNexus communities/clusters as internal evidence labels, not as a primary selection list;
- estimated LLM analysis units;
- warnings for broad, ambiguous, or unresolved objects.

The preview must not dump 1000 module names.

### 5.4 Focus Directions

Default focus directions must prioritize test design, runtime behavior, and failure propagation.

Default selected:

- key business flow / protocol flow;
- exception branches;
- exception propagation path;
- boundary values;
- long-running variable flip or wraparound risk;
- state machine and state transition;
- resource allocation and cleanup;
- concurrency / lock / async event ordering;
- observability: logs, return codes, counters, alarms;
- SFMEA input;
- C/C++ implicit logic: macros, `#ifdef`, function pointers, callback tables, switch-case dispatch, generated code, build configuration.

Default not selected:

- security risk.

Security risk can remain available as an optional focus direction, but it must be lower priority and off by default.

### 5.5 Report Selection

Reports are fixed templates controlled by checkboxes. The user can enable/disable templates but cannot mutate mandatory quality rules.

Default reports:

1. Project Structure Initial Understanding
2. Module Map
3. Targeted Source Reading Record
4. Key Business Flow Analysis
5. GitNexus Result Reliability Assessment
6. Test-Oriented Code Understanding

Optional reports:

- Requirements Traceability, only when requirements/design materials exist.
- Custom Report, only through structured fields.

Custom report input must be structured:

- title;
- audience;
- questions to answer;
- output format preference;
- max length.

Do not expose a raw "system prompt" box as the main customization mechanism.

### 5.6 User-Visible Guidance

The modal may include an editable guidance field, but it is only additive. It must not be allowed to override:

- report list schema;
- source validation rules;
- "do not trust GitNexus as final truth";
- empty-output failure rules;
- output chunk size limits;
- selected report templates.

If the user writes low-quality guidance, the system must normalize it into a usable plan rather than passing it raw into every LLM call.

## 6. Prompt Policy

Split prompt content into three layers.

### 6.1 Hidden System Constraints

These are not user-visible and cannot be removed:

- Prefer GitNexus structured output as navigation, not final evidence.
- Important conclusions must be verified against real source files, headers, macros, build config, and logs when available.
- For C/C++ projects, explicitly check macros, compile branches, function pointers, callbacks, switch-case dispatch, registration tables, inline/static functions, generated code, and platform-specific build conditions.
- If unsure, mark "待验证".
- Do not fabricate function names, files, structures, states, or logs.
- Do not treat absence of GitNexus edges as absence of real code calls.
- Do not read the entire repository indiscriminately.
- Focus on business flow, exception propagation, and test design value.
- Empty output, tiny output, malformed output, or obvious truncation is a failed generation, not a successful report.

### 6.2 Report Template Constraints

These are fixed by report type and may be visible as descriptions:

- required sections;
- required tables;
- required "待验证" handling;
- expected evidence references;
- maximum section length;
- whether Mermaid is required;
- whether SFMEA table is required.

### 6.3 User-Adjustable Content

User can adjust:

- analysis object text;
- focus direction selections;
- enabled reports;
- optional additional guidance;
- custom report structured fields.

User cannot directly edit hidden system constraints.

## 7. LLM Strategy for Internal AI Limits

The implementation must assume:

- context window around 192K;
- configured output limit around 8K;
- practical Chinese output truncation may happen around 2000 tokens;
- streaming may return empty content or provider-specific chunks that current parser misses.

Therefore:

1. Do not ask the LLM to generate one large report in one call.
2. Generate small evidence cards first.
3. Generate small report sections from cards.
4. Assemble Markdown programmatically.
5. Keep each LLM output target small.
6. Detect and retry empty/truncated sections.
7. If retry still fails, write an explicit failure block into the report and mark task/report status as failed or partial, not done.

Recommended output budgets:

- evidence card: 300-600 Chinese characters;
- source file reading card: 500-800 Chinese characters;
- one report section: 600-1000 Chinese characters;
- SFMEA table chunk: 5-8 rows per call;
- final assembled report: no single LLM call should be responsible for more than one major section.

## 8. Backend Data Model

Add typed schemas. Suggested file:

- Create `backend/app/schemas/workspace_analysis.py`

Required models:

```python
class AnalysisObject(BaseModel):
    id: str
    text: str
    kind: Literal["topic", "module", "flow", "file", "function", "mixed"] = "topic"
    priority: Literal["high", "medium", "low"] = "medium"


class FocusOptions(BaseModel):
    key_flows: bool = True
    exception_branches: bool = True
    exception_propagation: bool = True
    boundary_values: bool = True
    long_running_flip: bool = True
    state_machine: bool = True
    resource_cleanup: bool = True
    concurrency: bool = True
    observability: bool = True
    sfmea: bool = True
    cpp_implicit_logic: bool = True
    security_risk: bool = False


class ReportSpec(BaseModel):
    id: str
    title: str
    enabled: bool = True
    template_id: str
    custom: bool = False
    audience: str | None = None
    questions: list[str] = []
    max_sections: int | None = None


class LLMLimits(BaseModel):
    max_evidence_cards: int = 48
    max_files_per_object: int = 12
    max_functions_per_object: int = 30
    max_cards_per_report_section: int = 12
    max_output_chars_per_section: int = 1200
    retry_empty_output: int = 1


class AnalysisPlan(BaseModel):
    version: Literal["workspace-analysis-plan-v1"] = "workspace-analysis-plan-v1"
    analysis_objects: list[AnalysisObject]
    focus: FocusOptions = FocusOptions()
    reports: list[ReportSpec]
    user_guidance: str = ""
    llm_limits: LLMLimits = LLMLimits()


class ScopeCandidate(BaseModel):
    path: str | None = None
    symbol: str | None = None
    source: Literal["gitnexus", "repo_search", "material", "manual"]
    confidence: Literal["high", "medium", "low"]
    reason: str


class ResolvedAnalysisObject(BaseModel):
    object_id: str
    text: str
    candidate_files: list[ScopeCandidate] = []
    candidate_symbols: list[ScopeCandidate] = []
    related_communities: list[str] = []
    warnings: list[str] = []


class ScopePreview(BaseModel):
    workspace_id: str
    resolved_objects: list[ResolvedAnalysisObject]
    estimated_analysis_units: int
    warnings: list[str] = []
```

Exact names may differ, but the data must preserve these concepts.

## 9. Database Changes

Add storage for plan and scope preview.

Suggested table changes:

- `tasks.analysis_plan_json TEXT`
- `tasks.scope_preview_json TEXT`
- `tasks.report_plan_json TEXT`
- `workspaces.last_analysis_plan_json TEXT`
- `workspace_reports.status TEXT DEFAULT 'done'`
- `workspace_reports.error TEXT`
- `workspace_reports.metadata_json TEXT`

If the current lightweight migration style in `backend/app/database.py` is still used, add guarded `ALTER TABLE` statements there.

Do not break existing workspaces or existing report APIs.

## 10. API Contract

### 10.1 Get Default Plan

Add:

```http
GET /api/workspaces/{ws_id}/analysis/default-plan
```

Returns `AnalysisPlan` with default analysis object examples and default reports.

### 10.2 Preview Scope

Add:

```http
POST /api/workspaces/{ws_id}/analysis/preview
Content-Type: application/json

{
  "plan": { ...AnalysisPlan }
}
```

Returns `ScopePreview`.

Rules:

- 404 if workspace does not exist.
- 409 if workspace is not indexed.
- 400 if plan has no analysis objects.
- 200 with warnings if some objects are unresolved.

### 10.3 Start Analysis

Modify existing:

```http
POST /api/workspaces/{ws_id}/analyze
Content-Type: application/json

{
  "plan": { ...AnalysisPlan },
  "scope_preview": { ...ScopePreview }
}
```

Backward compatibility:

- If no body is provided, create a default legacy-compatible plan.
- The default legacy-compatible plan must still cap analysis fan-out. Do not fall back to all GitNexus communities.

Response may remain the current workspace object, but must persist plan and preview for the background task.

## 11. Scope Resolver

Create:

- `backend/app/services/workspace_scope_resolver.py`

Responsibilities:

1. Convert user analysis objects into bounded repo evidence.
2. Query GitNexus graph/clusters/search where available.
3. Use repo-side search as fallback.
4. Rank files/symbols by relevance.
5. Return `ScopePreview`.
6. Never return an unbounded module list.

Resolution sources:

- GitNexus graph nodes and edges;
- GitNexus cluster/community data;
- GitNexus grep/search endpoints if available;
- local repo path search via safe bounded `rg`;
- uploaded workspace materials;
- user manual paths/functions if provided.

Hard caps:

- max 12 candidate files per analysis object by default;
- max 30 candidate symbols per analysis object by default;
- max 8 related communities per analysis object;
- max 48 total evidence cards by default;
- all caps configurable through `LLMLimits`, but backend must enforce sane upper bounds.

If GitNexus is unavailable during preview but workspace was previously indexed:

- try cached graph if available;
- otherwise return a clear warning and use repo search fallback;
- do not fail unless no fallback evidence can be found and no user-provided files exist.

## 12. Analysis Pipeline Changes

Modify:

- `backend/app/services/workspace_pipeline.py`
- `backend/app/services/analysis_pipeline.py`

### 12.1 Workspace Pipeline

`WorkspacePipeline.run` must accept:

```python
async def run(
    self,
    ws_id: str,
    repo_path: Path,
    plan: AnalysisPlan | None = None,
    scope_preview: ScopePreview | None = None,
) -> None:
```

It must:

- persist the plan into the shadow task;
- preserve workspace materials as context;
- use GitNexus-only mode successfully when the removed Wiki dependency is unavailable;
- pass plan/scope data into `AnalysisPipeline`.

### 12.2 Analysis Pipeline

Change module analysis from:

```text
GitNexus communities -> one LLM analysis per community
```

to:

```text
AnalysisPlan + ScopePreview -> bounded analysis units -> evidence cards -> report sections
```

New concepts:

- `AnalysisUnit`: one unit per resolved analysis object, or per object group when objects are closely related.
- `EvidenceCard`: compact LLM or deterministic summary of source evidence.
- `SourceValidationRecord`: notes whether source files confirmed or contradicted GitNexus hints.

Rules:

- Estimated LLM analysis units must be roughly proportional to user analysis objects, not GitNexus community count.
- If 8 objects resolve to 6 related iSCSI topics and 2 FC topics, the pipeline should generate around 2-8 analysis units, not 1000.
- Closely related objects may be grouped when they share files/symbols.
- Unrelated protocol families such as iSCSI and FC should remain separate sections/groups.

### 12.3 Caching

Add or extend cache keys to include:

- repo path;
- commit hash if available;
- analysis object normalized text;
- focus options;
- selected report templates;
- source file content hash for evidence cards.

Cache these:

- scope preview;
- source evidence cards;
- analysis unit summaries;
- report section outputs.

Do not reuse cached summaries if source file hashes changed.

## 13. Report Generator Changes

Modify:

- `backend/app/services/report_generator.py`

### 13.1 Report Generation Architecture

Replace large one-shot report generation with:

```text
Evidence cards
  -> section draft calls
  -> validation
  -> retry/failure annotation
  -> programmatic Markdown assembly
```

The final report files should be assembled by code, not by asking the LLM to produce a complete large document in one call.

### 13.2 Required Report Files

When enabled, generate these files:

1. `项目结构初步理解.md`
2. `模块地图.md`
3. `源码定向阅读记录.md`
4. `关键业务流程分析.md`
5. `GitNexus结果可信度评估.md`
6. `测试视角代码理解.md`

If existing output names are numeric, preserve compatibility by mapping old IDs to new titles. Do not break existing report list UI.

### 13.3 Human-Readable Style

Reports must be for test engineers and engineers, not for an AI benchmark.

Required style:

- clear headings;
- short paragraphs;
- tables where comparison matters;
- call chains in fenced code blocks;
- "已验证" vs "待验证" labels;
- evidence references to files/functions where available;
- no huge undifferentiated paragraphs;
- no generic advice without file/function/process linkage.

### 13.4 Empty and Truncated Output Handling

A report section is invalid if:

- stripped content is empty;
- content is below a small threshold, for example less than 80 Chinese characters, unless the section explicitly has no evidence;
- content ends in an obvious incomplete sentence/table/code fence;
- required table/header is absent;
- provider returns no content chunks.

Behavior:

1. Retry once with a smaller prompt and stricter instruction.
2. If retry fails, write a visible failure block in that section.
3. Mark report status as `partial` or `failed`.
4. Do not mark workspace analysis as fully `done` when all reports are empty.

## 14. Frontend Changes

Create or modify:

- `frontend/src/app/workspaces/[id]/page.tsx`
- `frontend/src/components/workspaces/AnalysisTaskModal.tsx`
- `frontend/src/components/workspaces/AnalysisObjectEditor.tsx`
- `frontend/src/components/workspaces/FocusOptions.tsx`
- `frontend/src/components/workspaces/ReportPlanEditor.tsx`
- `frontend/src/components/workspaces/ScopePreview.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/types.ts`

### 14.1 Modal Layout

The modal should contain:

1. Analysis object editor
   - multi-line textarea;
   - one object per line;
   - support simple examples;
   - no raw GitNexus module list.
2. Focus directions
   - checkbox grid;
   - defaults selected as specified above;
   - security risk off by default.
3. Report selection
   - fixed report checkboxes;
   - optional custom report structured editor.
4. Optional guidance
   - textarea;
   - helper text saying it adds emphasis but cannot override report structure.
5. Scope preview
   - preview button;
   - resolved files/functions/groups;
   - warnings;
   - estimated analysis units.
6. Start button
   - disabled until at least one analysis object and one report are enabled;
   - if preview has not run, either run preview first or start with preview in one backend call.

### 14.2 UX Constraints

- Do not render 1000 GitNexus modules in the modal.
- Do not expose hidden system prompt.
- Do not allow "Generate Report" to start immediately without user confirmation.
- Make unresolved scope warnings visible but not terrifying.
- Show "GitNexus is used as navigation; source code remains final evidence" as a concise hint.

## 15. Report Template Details

### 15.1 Project Structure Initial Understanding

Must include:

- project purpose;
- main directories;
- selected analysis objects;
- relevant modules;
- key entry points;
- key data/control flows;
- key configs/build scripts;
- source files that must be read next;
- GitNexus hints that require validation.

### 15.2 Module Map

For each resolved group:

- responsibility;
- directories;
- key files;
- entry functions;
- data structures;
- call chain;
- dependencies;
- reverse dependencies;
- risks;
- questions requiring source verification.

### 15.3 Source Reading Record

For each targeted source file:

- file path;
- module/group;
- role in call chain;
- key functions;
- key structs/enums/macros;
- state variables;
- error codes;
- exception branches;
- GitNexus missed or suspicious call relations;
- macro/function pointer/callback/switch-case notes.

### 15.4 Key Business Flow Analysis

Must identify 3-5 key flows when evidence supports it. For each:

- flow goal;
- normal path;
- call chain;
- key states;
- inputs;
- outputs;
- exception paths;
- exception propagation;
- observability;
- test suggestions.

### 15.5 GitNexus Reliability Assessment

Must include:

- relationships likely correct;
- call chains likely correct;
- suspected missing edges;
- suspected false dependencies;
- macro-related limitations;
- callback/function-pointer limitations;
- switch-case/state-machine limitations;
- recommended validation tools: clangd, cscope, ctags, rg, compile database;
- suggestions for using GitNexus in this repo.

### 15.6 Test-Oriented Code Understanding

Must include:

- flow list;
- normal paths;
- exception paths;
- input factors;
- state factors;
- configuration factors;
- environment factors;
- observability points;
- exception injection points;
- potential fault modes;
- SFMEA draft;
- black-box test directions;
- gray-box test directions;
- white-box details that testers should not overdepend on.

SFMEA table format:

```markdown
| 功能/流程 | 故障模式 | 触发条件 | 异常注入点 | 异常传播路径 | 影响 | 可观测现象 | 严重度 | 发生概率 | 检测难度 | 建议测试 |
|---|---|---|---|---|---|---|---|---|---|---|
```

## 16. Performance Acceptance Criteria

### AC-P1: GitNexus Reuse

Given a workspace has already completed GitNexus indexing, starting report generation must not blindly force a full re-index. It may refresh graph data, but must prefer cached/indexed repo metadata.

### AC-P2: Bounded LLM Fan-Out

Given GitNexus returns 1000 communities and the user defines 8 analysis objects, the pipeline must not create 1000 module-analysis LLM calls.

Target:

- analysis units <= max(12, analysis object count * 2), unless user explicitly raises the cap;
- evidence cards <= configured `LLMLimits.max_evidence_cards`.

### AC-P3: GitNexus-Only Operation

If the removed Wiki dependency is offline or not installed, GitNexus-only workspace analysis must still produce selected reports when GitNexus and source files are available.

### AC-P4: Small Output Strategy

No LLM call should be responsible for generating a complete long report. Report sections must be generated and validated separately.

### AC-P5: Empty Output Failure

If the LLM returns empty content for a report section, the system must retry and then mark the section/report partial or failed. It must not silently write a successful empty report.

### AC-P6: Human-Readable Reports

Generated reports must be readable by test engineers. They must include concrete files/functions where available and mark uncertain claims as "待验证".

## 17. Implementation Tasks

### Task 1: Add Analysis Plan Schemas

Files:

- Create `backend/app/schemas/workspace_analysis.py`
- Modify `frontend/src/lib/types.ts`

Implement backend Pydantic models and matching frontend TypeScript types.

Tests:

- Add backend unit tests for validation defaults and invalid empty plan.

### Task 2: Add Database Fields

Files:

- Modify `backend/app/database.py`

Add guarded schema migrations for plan/scope/report metadata fields.

Tests:

- Extend database init tests if present.
- Verify migration works on an existing DB.

### Task 3: Add Default Plan and Preview API

Files:

- Modify `backend/app/api/workspaces.py`
- Create `backend/app/services/workspace_scope_resolver.py`

Add:

- `GET /api/workspaces/{ws_id}/analysis/default-plan`
- `POST /api/workspaces/{ws_id}/analysis/preview`

Tests:

- workspace missing -> 404;
- not indexed -> 409;
- empty objects -> 400;
- GitNexus unavailable with repo-search fallback -> 200 with warning;
- 1000 fake communities -> preview remains bounded.

### Task 4: Modify Analyze API Body

Files:

- Modify `backend/app/api/workspaces.py`
- Modify `backend/tests/test_workspaces_api.py`
- Modify `backend/tests/e2e/test_workspaces.py`

`POST /api/workspaces/{ws_id}/analyze` must accept optional plan and scope preview.

Tests:

- legacy no-body request still works;
- request with plan persists plan;
- running duplicate still returns current conflict behavior;
- invalid plan returns 400.

### Task 5: Pass Plan Through Workspace Pipeline

Files:

- Modify `backend/app/services/workspace_pipeline.py`
- Modify related tests that patch `WorkspacePipeline.run`

Ensure shadow task stores plan/scope and passes them into `AnalysisPipeline`.

Tests:

- workspace materials still included;
- removed Wiki dependency absent does not fail when tools are GitNexus-only;
- plan is visible to analysis pipeline.

### Task 6: Replace Community Fan-Out

Files:

- Modify `backend/app/services/analysis_pipeline.py`

Implement analysis units from `AnalysisPlan + ScopePreview`.

Tests:

- fake graph with 1000 communities and 8 objects creates bounded analysis units;
- related iSCSI objects can group together;
- FC objects remain separate when evidence does not overlap;
- cache reuse respects source hash and focus options.

### Task 7: Add Evidence Cards

Files:

- Modify `backend/app/services/analysis_pipeline.py`
- Optionally create `backend/app/services/evidence_card_builder.py`

Generate compact evidence cards from:

- GitNexus hints;
- source snippets;
- headers/macros/build config;
- workspace materials.

Tests:

- evidence card contains file/function references;
- unresolved evidence is marked "待验证";
- card size is bounded.

### Task 8: Rework Report Generation

Files:

- Modify `backend/app/services/report_generator.py`

Change from one-shot reports to section generation and programmatic assembly.

Tests:

- empty streaming output triggers retry;
- second failure marks report partial/failed;
- report includes required SFMEA table when test report enabled;
- no report is marked done when all generated content is empty.

### Task 9: Harden LLM Streaming Collection

Files:

- Modify `backend/app/llm/base.py`
- Modify `backend/app/llm/openai_compat.py`
- Modify provider tests or add new tests.

Requirements:

- detect empty collected streaming content;
- support provider chunk shapes used by internal OpenAI-compatible services where possible;
- expose enough metadata for report generator to classify empty/truncated output.

Tests:

- SSE stream with normal `delta.content`;
- stream with empty chunks;
- stream with alternative content shape if currently observed in internal provider logs;
- malformed stream does not produce successful empty output.

### Task 10: Build Frontend Modal

Files:

- Create `frontend/src/components/workspaces/AnalysisTaskModal.tsx`
- Create `frontend/src/components/workspaces/AnalysisObjectEditor.tsx`
- Create `frontend/src/components/workspaces/FocusOptions.tsx`
- Create `frontend/src/components/workspaces/ReportPlanEditor.tsx`
- Create `frontend/src/components/workspaces/ScopePreview.tsx`
- Modify `frontend/src/app/workspaces/[id]/page.tsx`
- Modify `frontend/src/lib/api.ts`
- Modify `frontend/src/lib/types.ts`

Tests:

- Add component/unit tests if the project has frontend test setup.
- Add or extend Playwright smoke test for modal open, preview, and start.

### Task 11: Update Documentation

Files:

- Modify `docs/USER_MANUAL.md`
- Modify `docs/DEPLOYMENT.md` only if config/env behavior changes.

Document:

- new analysis task modal;
- GitNexus-only behavior;
- why raw GitNexus module list is not shown;
- how to write good analysis objects.

## 18. Test Matrix

Backend tests:

- schema validation;
- scope resolver ranking and caps;
- API default plan;
- API preview;
- API analyze with plan;
- workspace pipeline plan persistence;
- analysis pipeline bounded fan-out;
- report generator empty-output handling;
- removed Wiki dependency absent with GitNexus present.

Frontend tests:

- generate button opens modal;
- default focus options match spec;
- security risk is off by default;
- user can edit analysis object text;
- preview warnings render;
- start analysis sends plan and scope preview.

Large workspace simulation:

- fake GitNexus graph with 1000 communities;
- user enters 8 analysis objects;
- assert analysis units are bounded;
- assert LLM call count is bounded through fake LLM client;
- assert generated reports are non-empty or explicitly partial/failed.

Regression tests:

- legacy `POST /api/workspaces/{ws_id}/analyze` without body still works;
- existing workspace report listing still works;
- existing export still works;
- chat module selector remains independent.

## 19. Open Questions

Technical open questions implementer may resolve:

- exact cache file/table location for evidence cards;
- exact internal representation of analysis units;
- exact visual component composition of the modal;
- whether report status should be `done/partial/failed` or reuse existing status values with metadata.

Product/value questions requiring owner decision:

- final official feature ID;
- whether custom reports are allowed in first implementation or deferred;
- maximum user-adjustable cap values exposed in UI;
- whether report file names should be Chinese canonical names or current numeric names with display aliases.

Default decision until owner says otherwise:

- implement custom report as structured optional but simple;
- preserve existing output file compatibility;
- cap analysis units conservatively;
- keep security risk off by default.

## 20. Definition of Done

This feature is done only when:

1. The workspace generate button opens a task modal.
2. The user can define analysis objects without seeing a raw GitNexus module list.
3. The backend can preview bounded scope.
4. The analysis pipeline uses plan/scope instead of all GitNexus communities.
5. GitNexus-only report generation works without the removed Wiki dependency.
6. Empty LLM output cannot silently become a successful report.
7. A fake 1000-community GitNexus graph with 8 analysis objects does not produce 1000 LLM calls.
8. Reports are assembled into readable Markdown with concrete evidence and "待验证" markers.
9. Backend and frontend tests cover the new flow.
10. Existing workspace analyze API remains backward compatible.
