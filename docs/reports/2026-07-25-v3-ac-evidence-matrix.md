---
feature_ids: [workflow-productization-v3]
topics: [acceptance, workflow, harness, security, quality, performance]
doc_kind: acceptance-matrix
created: 2026-07-25
---

# CodeTalk V3 AC Evidence Matrix

This is the release decision matrix for section 26 of the V3 goal. A green
unit test is evidence only for the contract it directly exercises. `verified`
means that the cited current implementation and evidence cover the whole AC;
`partial`, `blocked`, and `unverified` are not release-ready.

## Architecture

| AC | Status | Current evidence / remaining proof |
| --- | --- | --- |
| AC-ARCH-001 | verified | `run_snapshot_v3.json`, `WorkflowVersionStore`, `WorkbenchTaskRun`; Chromium `task_run_ee411b3017c14a83984ca6f93d02fca2` validates frozen snapshot before execution. |
| AC-ARCH-002 | partial | `AgentHarnessFacade` and SDK POC ADR isolate vendor SDKs, but no production SDK Adapter has passed all admission gates. |
| AC-ARCH-003 | verified | ADR-024 selects the existing local runner plus Harness Facade as the one durable runtime. |
| AC-ARCH-004 | partial | V2 compiled-plan compatibility projections remain. A final code-level audit must prove they cannot become an independently mutable user-visible workflow. |
| AC-ARCH-005 | verified | `/api/workbench/node-registry` drives node kind, ports, schema and inspector metadata; registry/inspector browser regressions are recorded. |
| AC-ARCH-006 | verified | `@xyflow/react` is a canvas adapter while `AuthoringGraphV2` and `WorkflowVersion` remain persistent contracts. |
| AC-ARCH-007 | partial | Builtin runs share task IDs and artifact roots; an external Agent run under the same contract is still blocked by deployment egress policy. |

## Security

| AC | Status | Current evidence / remaining proof |
| --- | --- | --- |
| AC-SEC-001 | blocked | Code and sandbox controls exist, but the required administrator-owned PCAP/gateway capture has not run. See `docs/security/zero-public-egress-verification.md`. |
| AC-SEC-002 | partial | Policy/tests deny telemetry, tracing, update and hosted-MCP paths; deployment capture must corroborate the shipped process behavior. |
| AC-SEC-003 | partial | macOS `sandbox-exec` DNS-denial subprocess evidence exists. An approved Agent route plus capture is still absent. |
| AC-SEC-004 | partial | Unit/sandbox policy rejects public targets before launch; needs browser workflow proof of one allowed configured Provider route in intranet deployment. |
| AC-SEC-005 | partial | SDK POC retains version/hash/license material outside product dependencies; final product SBOM and approved offline bundle evidence are missing. |
| AC-SEC-006 | partial | xyflow is locally bundled in the app; final network capture must prove no CDN/update/plugin/telemetry request in deployed designer. |

## Workflow Designer

| AC | Status | Current evidence / remaining proof |
| --- | --- | --- |
| AC-WF-001 | verified | Chromium canvas evidence covers pan, node drag and edge creation. |
| AC-WF-002 | verified | Edge selection/deletion regression is recorded with undo/redo. |
| AC-WF-003 | verified | Registry inspector supports add/edit/rename/delete input and output ports. |
| AC-WF-004 | verified | Real `file -> directory` drag is rejected immediately in Chinese. |
| AC-WF-005 | verified | Compiler and browser validation reject multiple data edges to scalar ports. |
| AC-WF-006 | verified | Published version and RunSnapshot hash regressions cover immutability. |
| AC-WF-007 | verified | Task wizard input-contract E2E switches form fields from published workflow inputs. |
| AC-WF-008 | verified | `@xyflow/react` is the production canvas implementation; legacy graph remains data only. |
| AC-WF-009 | partial | Registry drives default ports and inspector field ordering. Audit the remaining specialised inspector widgets before final release. |
| AC-WF-010 | verified | Chromium evidence covers box select, multi-select, batch move/delete, undo/redo and Fit View. Large-graph performance still belongs to AC-PERF-006. |
| AC-WF-011 | verified | Legacy migration browser tests preserve node/port/edge attributes and published versions. |
| AC-WF-012 | verified | Trial runs use the formal Compiler/Harness/Policy/Artifact route and are marked `not_a_formal_delivery`. |

## Harness And Skill Runtime

| AC | Status | Current evidence / remaining proof |
| --- | --- | --- |
| AC-HARNESS-001 | verified | Provider live-readiness artifacts are consumed by probe and run paths; deployment probe regressions cover both. |
| AC-HARNESS-002 | verified | Harness and AI-thread regression preserves multiline RunRequest content. |
| AC-HARNESS-003 | verified for builtin | Named workspace, document, MR and goal bindings appear in the input-consumption ledger. External Agent proof remains pending. |
| AC-HARNESS-004 | verified for builtin | RunSnapshot freezes provider, skills and MCP references; Flash/Pro browser runs use the frozen profile. |
| AC-HARNESS-005 | verified | Activity-aware timeout and provider-work preservation regressions cover active work beyond fixed idle timeout. |
| AC-HARNESS-006 | partial | Cancellation/failure classes are implemented and tested; restart/resume needs final real concurrency/restart evidence. |
| AC-HARNESS-007 | partial | Facade normalizes local/builtin events; a live external Agent is deployment-blocked. |
| AC-HARNESS-008 | partial | Session-capability fields exist; live provider resume behavior needs evidence. |
| AC-SKILL-001 | verified for builtin | Nine observable StageSpecs, gates and targeted repair completed in the 2026-07-25 SPDK deep run. |
| AC-SKILL-002 | verified for builtin | Source-driven artifacts cover flows, states, resources, boundary/wrap, concurrency, recovery and propagation. |
| AC-SKILL-003 | verified for builtin | `开发给测试讲代码.md` is deterministically rendered as the twelve required tester questions. The deep-contract validator rejects a non-empty file that omits any heading; browser-run Attempt 11 materialized all twelve sections. |
| AC-SKILL-004 | verified | Deterministic Source Evidence Pack and bounded stage prompts prevent source rediscovery; routing regression verifies branch-specific evidence. |

## Artifacts And Quality

| AC | Status | Current evidence / remaining proof |
| --- | --- | --- |
| AC-ART-001 | verified for builtin | Latest SPDK deep browser run delivered formal files without terminal-only output. |
| AC-ART-002 | partial | IDs are reconciled in traceability and RunSnapshot hashes exist; final Markdown/JSON hash audit remains required. |
| AC-ART-003 | verified for builtin | Cockpit separates delivery, supporting and diagnostic files. |
| AC-ART-004 | verified | Mind-map artifacts materialize from governed structured facts. |
| AC-ART-005 | verified | Node inspector renders output configuration as forms, without user JSON. |
| AC-QUALITY-001 | verified | Cockpit presents structure, facts, executability and coverage independently. |
| AC-QUALITY-002 | verified | Claim ledger tests and real quote/path/card failures block falsified anchors. |
| AC-QUALITY-003 | verified | Black-box boundary validators reject internal function steps. |
| AC-QUALITY-004 | verified | Quality retry preserves accepted rows and patches declared failed rows; real C-bit/risk-link retries covered. |
| AC-QUALITY-005 | verified | `READY_WITH_WARNINGS` / blocked status is separate from structural success. |
| AC-QUALITY-006 | failed | The earlier linked-artifact Flash review scored 62/100. A new real AI-thread review of the repaired nine-deliverable Flash/Pro run scored 72/100 and identified nonexistent symbols, Discovery `TargetAddress` overclaims, and expanded test-script mappings. The score remains below 80; test-path-to-scenario claims need deterministic/independent validation before another rubric run. |

## User Experience And Linking

| AC | Status | Current evidence / remaining proof |
| --- | --- | --- |
| AC-UI-001 | partial | V3 desktop/narrow layouts have browser evidence; final visual review screenshots are still required. |
| AC-UI-002 | verified | Cockpit failure presentation regression prevents alert panels obscuring summary and artifacts. |
| AC-UI-003 | verified | Bounded task/project/thread rails regressions prevent page-height accumulation. |
| AC-UI-004 | verified | AI thread preserves manual upward scroll while output streams. |
| AC-UI-005 | verified | Thought summaries collapse by default and raw terminal diagnostics remain technical-only. |
| AC-UI-006 | verified | Product-facing errors are Chinese/actionable with diagnostics behind disclosure. |
| AC-UI-007 | verified for builtin | Cockpit displays frozen input, provider, skills/MCP, stage output, elapsed time, failure and artifact state. |
| AC-LINK-001 | verified | Browser click creates a same-workspace AI thread from a completed run. |
| AC-LINK-002 | verified | Result thread renders and downloads nine linked deliverables. |
| AC-LINK-003 | verified for builtin | Follow-up context consumes attached artifacts and source context; repeat once for an uncached release artifact. |

## Performance

| AC | Status | Current evidence / remaining proof |
| --- | --- | --- |
| AC-PERF-001 | verified | `rapid` / `deep` are frozen execution policies of one WorkflowVersion. |
| AC-PERF-002 | unverified | Need five uncached rapid samples, P50/P95 and explicit 10-25 minute interpretation. |
| AC-PERF-003 | unverified | Need an uncached deep sample within the 45-90 minute target with continuous progress. |
| AC-PERF-004 | verified | Cached/reused stages are displayed; non-cache heavy tasks have anti-false-pass checks. |
| AC-PERF-005 | partial | Stage metrics exist but current cached final manifest does not preserve every aggregate metric; performance report must collect them. |
| AC-PERF-006 | unverified | Needs large artifact and concurrent-task browser evidence. |
| AC-PERF-007 | verified | Cockpit labels profile, target time and cache/reuse; docs prohibit treating retries as deep benchmarks. |

## Release Decision

### Final-quality audit consistency regression (2026-07-25)

Chromium started `task_run_79d4674fef6148408e526c77d89f3a77` from the
SPDK iSCSI task UI. The task completed with `quality_status=passed`. After
final delivery materialization, the task-level
`test_activity_quality_audit.json` and the Agent-visible
`quality_repairs/final_quality_audit.json` have the same SHA-256:
`8b9d28656fb343ec2cf81e0275be0211ce9f4711a5e5a4237575827d4424465c`.
Both record `deliverable`, score 100 and zero issues. The preceding repair-loop
snapshot is retained separately as
`pre_delivery_materialization_quality_audit.json`; it is no longer presented
as the final audit.

### DeepSeek Flash/Pro black-box dimension regression (2026-07-25)

Chromium started Attempt 10 (`task_run_94f9001f19c1404c9ee7fcb78c96fcac`)
from the SPDK iSCSI task UI. It completed after roughly 3 minutes 42 seconds,
but delivery was correctly blocked because the provider omitted the required
`resource_cleanup` dimension. This was not a source-evidence or artifact-file
failure: the Artifact Contract V3 validator passed and the final audit isolated
one missing black-box dimension.

The regression is now closed in two deterministic places: the black-box stage
capacity derives from the declared required-dimension contract, and first-pass
output is checked immediately for missing dimensions. A missing supported
dimension is materialized as an explicitly external-observable, evidence-bound
test hypothesis before final quality audit rather than waiting for an accidental
later model repair. The follow-up Chromium Attempt 11
(`task_run_bb4a68acc84b403386c8d930027bd62f`) completed with
`quality_status=passed`; its final audit is `deliverable`, score 100, zero
issues, and the task-level and Agent-visible final audits share SHA-256
`b5a261d6d23f789154b4f6eab472871bedc61ced167cc163a0383f5fe4986b2f`.
Its `开发给测试讲代码.md` contains all twelve required sections.

### 2026-07-25 Flash/Pro Basic Workflow Regression

Browser-run `task_run_b3b524bc6ad34e40bb1616b3a463c832` completed the published
"基础源码 + 设计文档报告（内置模型）" workflow with DeepSeek Flash/Pro after
3 minutes 41 seconds. Its final audit was `deliverable`, score `100`, with
zero findings and all twelve required black-box dimensions present. The UI
reported one cross-run cache hit, so this is a functional acceptance run, not
an uncached performance sample for AC-PERF-002. Screenshot evidence is stored
at `/Volumes/Media/codetalk-e2e-artifacts/v3-deepseek-flash-pro-risk-evidence-20260724/attempt-7-deliverable.png`.

The run also exposed a follow-up consistency gap: the Source Evidence Pack
materialized two test cards while the final report met its four-test-path
requirement by repository-path validation. This must be reconciled before the
stronger source-evidence provenance claim can be marked verified.

### 2026-07-25 DeepSeek Flash/Pro Quality-Retry Regression

Browser-run `task_run_2ca4eb1e31ed49baba52ed9eeed9ac09` is a real Chromium
quality-retry run for a newly created SPDK iSCSI Login deep-profile task. Its
frozen task input included the workspace, an uploaded design document, and the
literal user analysis target. Flash performed source/flow work; Pro performed
SFMEA, black-box and independent verification work. The final UI reported
`completed / passed / complete`, nine deliverables, 48/48 verified facts,
100% structure and 100% executability with zero blockers. The evidence bundle
is `/Volumes/Media/codetalk-e2e-artifacts/v3-deepseek-flash-pro-risk-evidence-20260724/`.

This proves that the repaired Artifact Contract path can normalize historical
quality-retry artifacts before reuse and publication. It is deliberately not
counted for AC-PERF-003: the UI records it as a 58-second quality review of
accepted artifacts, not a fresh deep execution. It also does not close
AC-QUALITY-006: the independent full-artifact rubric must be rerun after the
delivery-rendering repairs and score at least 80.

### 2026-07-26 Explicit Source-Card Follow-Up Regression

The same Chromium task-result thread was used to reproduce an independent
review failure around `SRC-09`. The real Flash/Pro delivery contains
`SRC-09` for `test/iscsi_tgt/chap/chap_common.sh`, lines `82-99`, symbol
`config_chap_credentials_for_target`. Before the regression fix, the AI thread
received only the beginning of the large evidence-card JSON and then repeated
its own stale statement that `SRC-09` did not exist.

The repair has three bounded parts: task-review context reserves priority for
the quality audit, claim/fact ledgers and evidence cards; an explicit `SRC-*`
lookup extracts a compact verified card rather than clipping the beginning of
the full JSON; and a matching card is answered deterministically instead of
asking a model to recollect a verified fact. Chromium evidence is retained as
`attempt15-deterministic-src09-pass.png` and
`attempt15-deterministic-src09-pass.txt` in
`/Volumes/Media/codetalk-e2e-artifacts/v3-deepseek-flash-pro-risk-evidence-20260724/`.
The final result showed the correct path, `L82-L99`, and symbol in zero seconds;
this is a metadata lookup, not a non-cached workflow benchmark and therefore
does not alter the performance ACs or close AC-QUALITY-006.

The associated narrow independent-review Chromium check is retained as
`attempt17-pro-isolated-short-review.png` and
`attempt17-pro-isolated-short-review.txt`. It used the configured DeepSeek Pro
thread route and correctly reported `SRC-09:L82` as present in the evidence
index, then identified a bounded CHAP negative-scenario mapping risk without
repeating earlier full-review history. This establishes review-context
isolation and evidence-card readability only; it is not a substitute for the
required full independent-quality rubric.

The current code is **not releasable as V3**. No push to `origin/feat` and no
goal completion is permitted until every `partial`, `blocked`, and `unverified`
row has direct current evidence, including the deployment-owned security and
external-Agent prerequisites.
