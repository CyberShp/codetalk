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
| AC-ARCH-002 | partial | `AgentHarnessFacade` now accepts an injected `ProviderAdapter`; the default `LocalCliProviderAdapter` preserves the existing CLI path while the facade keeps stable task results, lifecycle events and artifact semantics. SDK POC ADR isolates vendor SDKs, but no production SDK Adapter has passed all admission gates. |
| AC-ARCH-003 | verified | ADR-024 selects the existing local runner plus Harness Facade as the one durable runtime. |
| AC-ARCH-004 | verified | `WORKBENCH_V2_ENABLED=false` is now rejected at configuration load; V3 migrations and task APIs always initialize. Legacy URLs unconditionally redirect to `/tasks`, `/workflows`, or `/semantic-library`, while historical artifacts remain read-only compatibility data. Chromium `workbench-v2-release-real.spec.ts` verifies all three redirects and the V3 pages at 1440/1280/1024. |
| AC-ARCH-005 | verified | `/api/workbench/node-registry` drives node kind, ports, schema and inspector metadata; registry/inspector browser regressions are recorded. |
| AC-ARCH-006 | verified | `@xyflow/react` is a canvas adapter while `AuthoringGraphV2` and `WorkflowVersion` remain persistent contracts. |
| AC-ARCH-007 | partial | Builtin runs share task IDs and artifact roots; an external Agent run under the same contract is still blocked by deployment egress policy. |

## Security

| AC | Status | Current evidence / remaining proof |
| --- | --- | --- |
| AC-SEC-001 | blocked | Code and sandbox controls exist, and the capture collector now records redacted before/after CodeTalk process snapshots plus PCAP hashes without changing network configuration. `26890d01` additionally routes GitNexus (full/light), CGC, Semgrep, CodeCompass, workspace/chat/evidence reads, API proxy and remote Docker TCP through deployment endpoint admission before client creation. The required administrator-owned PCAP/gateway capture has not run. See `docs/security/zero-public-egress-verification.md`. |
| AC-SEC-002 | partial | Policy/tests deny telemetry, tracing, update and hosted-MCP paths, while tool clients now disable inherited proxies and require a deployment-approved endpoint. Deployment capture must corroborate the shipped process behavior. |
| AC-SEC-003 | partial | macOS `sandbox-exec` DNS-denial subprocess evidence exists. An approved Agent route plus capture is still absent. |
| AC-SEC-004 | verified | A real settings-page run approved only `api.deepseek.com` at deployment level and completed configured Flash/Pro inference; the Chromium settings regression separately enters `https://example.com/v1` and receives `运行时出站策略拒绝：host_not_allowlisted` before connection. Policy unit coverage confirms approved hostname and narrow model endpoint behavior. Evidence: `/Volumes/Media/codetalk-e2e-artifacts/v3-deepseek-flash-pro-20260726/run5/`, `/Volumes/Media/codetalk-e2e-artifacts/v3-intranet-policy-ui-20260726/`. |
| AC-SEC-005 | partial | `scripts/generate-offline-sbom.py` creates a local-only manifest plus a fail-closed `license-review.json` from backend requirements, frontend lockfile and installed metadata. The 2026-07-27 run recorded 692 components, 623 resolved local licenses, 69 explicit `UNKNOWN` items and no unresolved backend dependency. `generate-offline-bundle-manifest.py` now also records SHA256/name/version/license/platform for the 64 local SDK POC artifacts, correctly leaving Claude SDK's “SEE LICENSE IN README.md” as an explicit unknown. A fresh isolated Python 3.11 environment installed `openai-agents==0.18.3` from that bundle with `pip --no-index` and successfully imported `agents` (`freeze.txt` SHA256 recorded). Human license approval and a complete platform-specific CodeTalk offline-bundle install evidence remain required. |
| AC-SEC-006 | partial | xyflow is locally bundled in the app; final network capture must prove no CDN/update/plugin/telemetry request in deployed designer. |

## Workflow Designer

| AC | Status | Current evidence / remaining proof |
| --- | --- | --- |
| AC-WF-001 | verified | Chromium canvas evidence covers pan, node drag and edge creation; the 2026-07-27 live `@xyflow/react` run also used left-button node drag on a temporary saved copy. |
| AC-WF-002 | verified | Edge selection/deletion regression is recorded with undo/redo; the 2026-07-27 live canvas run selected an existing edge, pressed Delete, and restored it with the visible undo action. |
| AC-WF-003 | verified | Registry inspector supports add/edit/rename/delete input and output ports. |
| AC-WF-004 | verified | Real `file -> directory` drag is rejected immediately in Chinese. |
| AC-WF-005 | verified | Compiler and browser validation reject multiple data edges to scalar ports. |
| AC-WF-006 | verified | Published version and RunSnapshot hash regressions cover immutability. |
| AC-WF-007 | verified | Task wizard input-contract E2E switches form fields from published workflow inputs. |
| AC-WF-008 | verified | `@xyflow/react` is the production canvas implementation; legacy graph remains data only. |
| AC-WF-009 | verified | The wizard's input, Agent and output steps now render the same backend Node Registry and Node Inspector as the canvas. Chromium creates, edits, compiles, trial-runs and publishes through this path; canvas regression covers ports, type checks and persisted edits. Evidence: `/Volumes/Media/codetalk-e2e-artifacts/v3-registry-wizard-20260726/`. |
| AC-WF-010 | verified | Chromium evidence covers box select, multi-select, batch move/delete, undo/redo and Fit View. Large-graph performance still belongs to AC-PERF-006. |
| AC-WF-011 | verified | Legacy migration browser tests preserve node/port/edge attributes and published versions. |
| AC-WF-012 | verified | Trial runs use the formal Compiler/Harness/Policy/Artifact route and are marked `not_a_formal_delivery`. |

## Harness And Skill Runtime

| AC | Status | Current evidence / remaining proof |
| --- | --- | --- |
| AC-HARNESS-001 | verified | Provider live-readiness artifacts are consumed by probe and run paths; deployment probe regressions cover both. The 2026-07-26 real Chromium Codex preflight regression also covers mixed Agent/independent-audit failures and verifies that the cockpit prioritizes the actual Agent startup blocker. |
| AC-HARNESS-002 | verified | Harness and AI-thread regression preserves multiline RunRequest content. |
| AC-HARNESS-003 | verified for builtin | Named workspace, document, MR and goal bindings appear in the input-consumption ledger. External Agent proof remains pending. |
| AC-HARNESS-004 | verified for builtin | RunSnapshot freezes provider, skills and MCP references; Flash/Pro browser runs use the frozen profile. |
| AC-HARNESS-005 | verified | Activity-aware timeout and provider-work preservation regressions cover active work beyond fixed idle timeout. |
| AC-HARNESS-006 | verified for builtin | Cancellation/failure classes are implemented and tested. Real Chromium Flash evidence covers both terminal restoration and an active provider call interrupted by backend restart, with the active node, public reason and retry path preserved. Native provider-session resume remains separately unverified under AC-HARNESS-008. |
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
| AC-ART-002 | verified for builtin | Attempt 12 (`task_run_d2fb76735d5648a7a368e7d95a8489b3`) was started from the browser on 2026-07-26. `artifact_alignment_audit.json` passed all four deterministic pairs: evidence `12/12`, flow `1/1`, SFMEA `12/12`, and black-box cases `13/13`; it records SHA256 values for each structured JSON and rendered Markdown delivery. |
| AC-ART-003 | verified for builtin | Cockpit separates delivery, supporting and diagnostic files. |
| AC-ART-004 | verified | Mind-map artifacts materialize from governed structured facts. |
| AC-ART-005 | verified | Node inspector renders output configuration as forms, without user JSON. |

## 2026-07-26 Flash Regression Addendum

`task_run_5adb8ec688824611be1ddc10c1e374af` is a real Chromium, Flash-only SPDK
run. It reached `completed / passed / complete`, a `deliverable` quality audit
at 100 with zero issues, 24/24 verified claims, and a ready task acceptance
audit with 70 required checks and no missing items. Its artifact root is
`/Volumes/Media/codetalk-e2e-artifacts/v3-regular-stage-governance-flash-only-final42-20260726/`.
This strengthens builtin workflow/quality/delivery evidence only; it does not
turn any remaining `partial`, `blocked`, or independent-performance AC into a
verified release condition.

> 2026-07-26 gate correction: the Flash-only records above used the same
> model identifier for generation and audit. They remain real functional,
> evidence and performance records, but no longer count as independent quality
> approval after `independent_behavior_validation_unavailable` was made
> fail-closed. A final deliverable must use a different audit model or an
> independent Agent.

The latest same-Flash browser run (`task_run_a99d941e4c1c4e01999e7af042808713`)
confirms that this correction is effective against real SPDK output: source evidence,
flow, SFMEA, black-box and report drafts are physically materialized, but the cockpit
ends at `quality_blocked` when independent behavior review is unavailable and the
professional fact gate detects an invalid CHAP Login-response assertion and an unsafe
`/dev/sdX` black-box instruction. Evidence:
`/Volumes/Media/codetalk-e2e-v3/flash-same-model-20260726-232123/`.

### Flash 生成 + Pro 独立审计正式交付（2026-07-27）

真实 Chromium 运行 `task_run_0f4f8ef34825433a8dcee67c4ababad4` 使用 Flash 生成源码、
流程、SFMEA 与黑盒交付，再由不同模型标识的 Pro 执行独立质量核验。最终
`task_acceptance_audit.json` 为 `ready`，`final_quality_audit.json` 为
`deliverable / 100 / 0 issues`，而不是同模型路径的 `quality_blocked`。完整证据位于
`/Volumes/Media/codetalk-e2e-v3/flash-pro-independent-audit-20260727-001211/`；Playwright
端到端用时 `4.9m`。该记录是基础内置模型工作流的独立交付证据，仍不替代深度性能、外部
Agent 或部署级流量捕获验收。

### 深度 iSCSI 质量收敛回归（2026-07-27）

真实 Chromium 从驾驶舱的“修复质量问题并重试”按钮启动 Attempt 13
`task_run_8799d722df1848a787007176eab583ec`，针对 SPDK iSCSI Login/CHAP、
设计文档和资源/并发/恢复范围执行。此前两项真实阻断分别来自报告标题无法定位到
`black_box_cases.json` 的稳定行，以及中文“不会清除 C bit”这一正确否定语义被旧门禁
误判。修复后运行显示 `completed / passed / complete`；最终质量审计为
`deliverable`、0 issues、57/57 已验证事实、0 矛盾、0 证据不足，驾驶舱显示 9 个可交付文件。
浏览器截图位于
`/Volumes/Media/codetalk-e2e-artifacts/20260727-attempt13/quality-pass.png`。
这证明定向修复可在真实交付字节上收敛；不替代外部 Agent、部署级抓包或 40--90 分钟深度性能验收。

### 覆盖处置警告不再伪装为全绿（2026-07-27）

后续复核发现上述 Attempt 13 的 `judge_report.json` 仍包含 46 条
`need_verify` 覆盖处置项，`coverage_judge` 实际为 `READY_WITH_WARNINGS / 80`。
旧汇总仅依据 `coverage_breadth` 决定顶层状态，因此会把这类已被独立覆盖审查标记的缺口
显示为 `deliverable / 100`。`7c36a049` 现将两个覆盖轴共同纳入最终状态：速度型为
`warning / 80 / 受限交付`，深度型为 `needs_rework / 80` 并给出
`source_driven_coverage_incomplete` 的定向修复项。以同一冻结审计字节重算已验证该结果。
因此 Attempt 13 仅保留为“真实浏览器定向修复与交付链”证据，不再作为深度质量通过或
独立准确度验收依据。

### 基础对照 B：设计文档输入与 Flash（2026-07-26）

`task_run_055485884aae4a819a21c9b21ef684d2` 通过真实浏览器完成设置、工作空间、
上传设计文档、任务向导和驾驶舱等待；当时显示 `completed / passed / complete`、质量 100、
零问题与完整交付。它还验证修复后的 source-analysis metrics 在真实 cache-miss 运行中记录
`provider_call_count=1` 与 `total_duration_ms=12597.4`。详细证据见
`2026-07-26-basic-builtin-flash-e2e.md`。这是 builtin 对照 B 的验收，不替代外部 Agent、
深度档、部署抓包或并发大产物门禁。

同一对照工作流的后续深度型 Flash 浏览器运行
`task_run_88c41392b2554d54952e3c2bccbe5cb7` 于 2026-07-26 以
`completed / passed / complete`、质量 100、零问题和完整交付结束，点击至终态为
`178574ms`。它验证最终报告级定向修复在真实模型输出上的收敛；但运行远短于 40--90
分钟，因此不改变 AC-PERF-003 的 `unverified` 状态。两者均因同模型 Flash 审计在新门禁
下改记为“待独立复核”，不再构成 AC-QUALITY-006 的通过证据。

| AC-QUALITY-001 | verified | Cockpit presents structure, facts, executability and coverage independently. |
| AC-QUALITY-002 | verified | Claim ledger tests and real quote/path/card failures block falsified anchors. |
| AC-QUALITY-003 | verified | Black-box boundary validators reject internal function steps. |
| AC-QUALITY-004 | verified | Quality retry preserves accepted rows and patches declared failed rows; real C-bit/risk-link retries covered. |
| AC-QUALITY-005 | verified | `READY_WITH_WARNINGS` / blocked status is separate from structural success. |
| AC-QUALITY-006 | verified for builtin | Chromium-linked independent review of Attempt 35 used the configured DeepSeek Pro audit model and returned `85/100` with all six rubric dimensions. The review consumed deterministic full-delivery coverage, report scoring-method lines and claim-validation scope, preventing prior truncation-based false findings. |

## User Experience And Linking

| AC | Status | Current evidence / remaining proof |
| --- | --- | --- |
| AC-UI-001 | verified | Visual browser review at 1440x900 and 390x844 covers the workflow wizard, task cockpit and AI thread. All inspected views have no horizontal overflow; the desktop stays tool-compact and narrow screens use a non-overlapping single-column layout. Screenshots and browser metrics: `/Volumes/Media/codetalk-e2e-artifacts/v3-ui-compact-review-20260726/`. |
| AC-UI-002 | verified | Cockpit failure presentation regression prevents alert panels obscuring summary and artifacts. |
| AC-UI-003 | verified | Bounded task/project/thread rails regressions prevent page-height accumulation. |
| AC-UI-004 | verified | AI thread preserves manual upward scroll while output streams. |
| AC-UI-005 | verified | Thought summaries collapse by default and raw terminal diagnostics remain technical-only. |
| AC-UI-006 | verified | Product-facing errors are Chinese/actionable with diagnostics behind disclosure. |
| AC-UI-007 | verified for builtin | Cockpit displays frozen input, provider, skills/MCP, stage output, elapsed time, failure and artifact state. |
| AC-LINK-001 | verified | Browser click creates a same-workspace AI thread from a completed run. On 2026-07-27 Attempt 15 clicked `围绕本次运行继续分析` and opened `conv_fc27b376c65a4c50a65e4edcf3236be0` for the same SPDK workspace; the temporary thread was then deleted through the UI. |
| AC-LINK-002 | verified | Result thread renders and downloads nine linked deliverables. The Attempt 15 follow-up thread visibly reported `执行 completed · 质量 passed · 交付 complete` and `已旁挂交付件 9 个文件` before the user sent a question. |
| AC-LINK-003 | verified for builtin | Follow-up context consumes attached artifacts and source context; repeat once for an uncached release artifact. |

## Performance

| AC | Status | Current evidence / remaining proof |
| --- | --- | --- |
| AC-PERF-001 | verified | `rapid` / `deep` are frozen execution policies of one WorkflowVersion. |
| AC-PERF-002 | partial | Five real, cache-miss-only Flash browser samples completed with P50 `3:22` and P95 `3:52`; all delivered. This is below the current 8-20 minute target, so it is evidence against the current estimate rather than a completed timing acceptance. See `2026-07-26-flash-rapid-uncached-e2e-baseline.md`. |
| AC-PERF-003 | unverified | Need an uncached deep sample within the 40-90 minute target with continuous progress. |
| AC-PERF-004 | verified | Cached/reused stages are displayed; non-cache heavy tasks have anti-false-pass checks. |
| AC-PERF-005 | verified for builtin | A real Chromium/Flash run on 2026-07-27 preserved `source_analysis` stage metrics after the complete workflow: `attempt_count=1`, `provider_call_count=1`, `provider_wait_ms=1003.6`, `output_tokens=76`, `total_duration_ms=9820.5`, `finish_reason=stop`. The browser E2E now fails if a later repair/reuse path erases those metrics. Evidence: `/Volumes/Media/codetalk-e2e-v3/v3-flash-metrics-refresh-2026-07-26T19-28-04-858Z/`. |
| AC-PERF-006 | verified for builtin | Two independent Chromium sessions ran the published built-in workflow concurrently against SPDK without cross-run corruption (117 artifacts and about 5.3 MiB per run). A separate real Chromium run opened a 61,406-byte SPDK report in the bounded preview, surfaced the truncation notice, and downloaded the complete file directly from the modal. Evidence: `/Volumes/Media/codetalk-e2e-v3/flash-concurrent-two-browser-fixed-20260727-002820/`, `/Volumes/Media/codetalk-e2e-v3/flash-large-artifact-download-fixed-20260727-004421/`. |
| AC-PERF-007 | verified | Cockpit labels profile, target time and cache/reuse; docs prohibit treating retries as deep benchmarks. |

### Flash deep-profile evidence and repair convergence (2026-07-27)

Chromium ran the real “source workspace + design document” SPDK workflow in
the `deep` profile with DeepSeek Flash for generation and the same Flash model
configured as the audit model. The final run was not a false delivery: it
ended `quality_blocked` because same-model review is intentionally not treated
as independent. All other deep-work gates converged: eight provider calls,
29,037 generated output tokens, 161,223 ms provider wait, four exploration
branches, and every branch's routed evidence proof passed. The repair loop
also removed all observed black-box boundary and professional-fact conflicts.

- Evidence root: `/Volumes/Media/codetalk-e2e-v3/v3-deep-flash-path-proof-20260727-012831/`
- Task run: `task_run_4ef705f65d6d4879b14d3f22b374668d`.
- Browser result: `1 passed`; duration was about three minutes. This is real
  deep-profile functional and quality-governance evidence, but remains too
  short for AC-PERF-003's 40–90 minute workload acceptance, and cannot close
  independent-quality acceptance while generator and auditor are identical.

The cockpit now exposes a bounded “深度执行证明” summary for this run instead
of leaving users to infer work from duration alone: model-call count, output
tokens, Provider wait, exploration-branch count and reuse state are visible;
raw prompts, source routes and provider diagnostics remain private. A fresh
Chromium inspection against the persisted real run passed on 2026-07-27:
`frontend/e2e/v3-deep-work-proof-real.spec.ts` (`1 passed`, 6.8s). This is a
UI observability improvement, not evidence that the three-minute run meets
AC-PERF-003.

### Five-run Flash uncached rapid baseline (2026-07-26)

Playwright created five separate iSCSI SPDK tasks through the product UI with
DeepSeek `deepseek-v4-flash` for both generation and quality review. Every
model stage was a cache miss and `reused=false`; all five completed as
`completed / passed / complete` with `deliverable` quality and zero issues.
The observed P50 was `3:22` and nearest-rank P95 `3:52`, not the published
8-20 minute speed estimate. The local deterministic source/flow stages and
parallelized bounded model stages explain the result; no artificial delay was
added. Full per-run evidence, artifacts and the observability follow-up are in
`2026-07-26-flash-rapid-uncached-e2e-baseline.md`.

### Two concurrent Chromium Flash runs after audit-worker repair (2026-07-27)

The first attempt to collect a fresh concurrent browser proof exposed a real
runtime defect instead of a provider or UI issue: one run entered the expected
same-model quality block, while the other terminated with
`Quality audit worker exited without a result (exit=1)`. The task was still
executing the local final quality audit from FastAPI's background thread, which
then performed a nested POSIX `fork`. On macOS that can fork a multithreaded
interpreter after concurrent model work and make the child exit before writing
its audit result.

The repair keeps the main-thread process-isolation path, but executes this
strictly local file/JSON audit in a deadline-bound thread whenever the lifecycle
already runs in a background thread. A regression test prevents reintroducing
the nested fork. Chromium then repeated the complete user flow in two workers:
settings configuration, existing-workspace reuse through the visible UI, design
document upload, target entry, profile selection and click-to-run.

- Evidence root: `/Volumes/Media/codetalk-e2e-v3/flash-concurrent-two-browser-fixed-20260727-002820/`
- `task_run_7de2e9a9ef234d6ea59f953a0275e7db`: `quality_blocked / blocked / none`, 129,055 ms, 117 materialized artifacts, 5,412 KiB.
- `task_run_9688b5dc85af4b32ade03e26e9ddab92`: `quality_blocked / blocked / none`, 149,637 ms, 117 materialized artifacts, 5,344 KiB.
- Both runs record `deterministic_only` finalization with independent behavior
  validation unavailable because the generator and auditor were intentionally
  the same Flash model. This is an expected quality block, not an execution
  failure or a false delivery.

This closes the concurrent task stability portion of AC-PERF-006. The following
browser run closes its separate bounded-preview/download portion.

### Bounded long-artifact preview and direct download (2026-07-27)

The concurrent run produced a 72 KiB `report.md`, but the cockpit had never
proven the user action after its 50,000-character preview boundary. A real
browser regression exposed that the old list download button sat behind the
modal; the preview itself told users to download the complete file, but the
modal intercepted that click. The preview header now provides its own visible
`下载完整文件` control. Chromium repeated the real workflow and then opened
the generated 61,406-byte SPDK report, observed `内容较长，预览已截断，请下载完整文件。`,
and downloaded the corresponding full `report.md` before closing the dialog.

- Evidence root: `/Volumes/Media/codetalk-e2e-v3/flash-large-artifact-download-fixed-20260727-004421/`
- This is browser interaction with a real model-generated artifact, not a
  routed response or fixture. Preview stays bounded while the download remains
  the complete exact artifact endpoint.

### V3 terminal-run persistence across backend restart (2026-07-27)

The V3 cockpit must not rely on browser memory to display a finished run. A
fresh Chromium session configured DeepSeek Flash in Settings, created a real
SPDK workspace, uploaded the design-document input, created the basic
source-plus-document task, selected the rapid profile, and clicked run. The
real run reached the expected fail-closed `已阻断` terminal state and
materialized a 63.8 KiB `report.md`. The test then stopped its isolated backend
process, started the backend again against exactly the same data directory, and
navigated the browser back to the same task/run URL. The restored cockpit still
showed the task title, `已阻断`, and the same `report.md` delivery item.

- Evidence root: `/Volumes/Media/codetalk-e2e-v3/v3-restart-real-20260727-005829/`
- Chromium result: `1 passed (2.4m)`.
- This proves durable terminal task/artifact restoration for the builtin Flash
  route. It deliberately does not claim an interrupted external provider
  session was resumed natively; that remains the open portion of
  AC-HARNESS-006/008.

### V3 in-flight provider interruption after backend restart (2026-07-27)

The companion reliability test now covers the failure case that terminal
restoration alone cannot prove. Chromium configured DeepSeek Flash through the
Settings UI, created a real SPDK workspace and source-plus-design-document
task, then waited until the `business_flow` stage had emitted both `provider
submitted` and live output checkpoints. The isolated backend process was then
stopped while Flash was still generating and restarted against the identical
data directory. Reopening the same cockpit showed `已中断`, changed the active
`analyze` node to `运行中断`, retained the public explanation `后端服务重启，本次
工作流运行已中断，请重新运行。`, and exposed the normal retry path.

- Evidence root: `/Volumes/Media/codetalk-e2e-v3/v3-inflight-restart-2026-07-26T19-13-56-716Z/`
- Chromium result: `1 passed (32.3s)`.
- This proves the product's explicit unrecoverable-provider behavior for the
  builtin route. It does **not** claim native provider session continuation;
  that remains AC-HARNESS-008.

## Release Decision

### DeepSeek Flash final-delivery convergence regression (2026-07-26)

Chromium created a fresh SPDK iSCSI Login task from the product UI after
configuring `deepseek-v4-flash`. Run
`task_run_1ef03fa103db4a818f8997789882dbd0` completed in `195764ms` with
`completed / passed / complete`. Its final task audit is `deliverable`, score
100, with zero findings; the acceptance audit has no missing required checks;
the final Claim Ledger is `24/24 verified`; and the nested source-driven judge
is `READY_WITH_WARNINGS` with facts, structure and executability all passed.
The remaining coverage entries are explicit `need_verify` warnings, not claimed
coverage or blocking failures. This run exercises the final SFMEA normalization,
black-box risk-ID reconciliation, source-driven governance refresh, and
nonexistent-test-path downgrade paths. Evidence:
`/Volumes/Media/codetalk-e2e-artifacts/v3-regular-stage-governance-flash-only-final55-20260726/`.
It is a Flash functional/convergence regression, not a five-run uncached
performance baseline, deep-profile benchmark, external-Agent acceptance, or
full V3 release decision.

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

### 2026-07-26 Flash/Pro final-SFMEA contract regression

Chromium clicked “启动新运行” for Attempt 35
(`task_run_47fd0993df044db2aacdea0fc3018279`) after the backend loaded
`49502140`. The browser page reported `已完成 / 通过 / 交付完整`, nine
downloadable files, 12 final SFMEA rows, and `36/36` verified facts with zero
blockers. Flash performed the flow branch; Pro performed SFMEA and black-box
generation. The final SFMEA stage retained its original Pro execution metrics
after deterministic claim repair: one provider call, 85,496.1 ms provider wait,
5,772 output tokens, `provider_finish_reason=stop`, and
`finish_reason=deterministic_claim_repair`. Screenshot evidence is
`/Volumes/Media/codetalk-e2e-artifacts/v3-deepseek-flash-pro-risk-evidence-20260724/attempt35-completed.png`.

Source analysis was a declared cache hit, so the 3 minutes 31 seconds elapsed
time is functional and observability evidence only. It does not close the
uncached deep performance acceptance criterion.

### 2026-07-26 Flash/Pro rapid alignment regression

Chromium started a new task from the settings page after configuring
`deepseek-v4-flash` for generation and `deepseek-v4-pro` for independent
quality validation. The first browser run exposed a deterministic product
contradiction: rapid materialized `flow_cards.json`, but did not materialize
its matching tester-facing flow Markdown while the mandatory alignment audit
always checked that pair. The task correctly stopped at `quality=blocked`;
no model output was silently accepted.

`af83ab19` adds the missing rapid flow delivery without weakening the audit.
The fresh isolated browser rerun `task_run_538caaaf9f6c4c62ba4b89769f4729d1`
completed in `187500ms`, with `deliverable` quality score `100`, zero issues,
and all evidence/flow/SFMEA/black-box alignment pairs passed. It used a real
SPDK workspace and uploaded design document. Three targeted quality-repair
passes occurred, so it is functional and quality evidence, not an uncached
rapid performance baseline. Artifacts: `/Volumes/Media/codetalk-e2e-artifacts/v3-deepseek-flash-pro-20260726/run5/`.

### 2026-07-26 DeepSeek V4 Flash/Pro isolated rapid regression

Chromium configured and probed the official DeepSeek V4 routes from the settings
page, then created a fresh SPDK workspace, uploaded the design document, chose
the published built-in rapid workflow and started the task through the task
wizard. Run `task_run_7931e9af092243ca9720d2ee0c32db6b` completed in
`197509ms` with `completed / passed / complete`; the final acceptance audit and
artifact-alignment audit both passed. The initial source, flow, SFMEA and
black-box stage calls were cache misses. Flash handled source/flow work and Pro
handled SFMEA/black-box work: source-analysis waited 1.1s, business-flow 16.5s,
SFMEA 73.1s and black-box 42.1s. Two targeted same-run quality-repair passes
reused accepted artifacts and made no second full source call. This is valid
functional, quality and V4 route evidence, but is deliberately not counted as
one of the five uncached rapid performance samples because the rapid policy's
published 10-25 minute interpretation still needs a representative sample set.
Artifacts: `/Volumes/Media/codetalk-e2e-artifacts/v3-rapid-five-samples-20260726/run3/`.

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

### 2026-07-26 Final Professional-Audit Feedback Regression

Chromium started Attempt 15 (`task_run_1cdfee3be4b64fa1a9a8cae28dfd568f`)
from the same SPDK task's `修复质量问题并重试` control after a final-audit
mapping failure had been reproduced. The earlier attempts correctly blocked
`BC-05`: they incorrectly used `test/iscsi_tgt/multiconnection/multiconnection.sh`
as evidence for same-Target concurrent Login behavior. The final audit reports
row identifiers, while the prior deterministic repair only matched a scenario
field; it therefore could not repair this late-stage finding.

The task-level repair path now consumes only final audit findings, resolves the
named structured row, re-renders Markdown, and re-audits the canonical bytes.
Attempt 15 completed with `quality_status=passed`, `delivery_status=complete`,
zero blockers, and nine deliverables. The accepted `BC-05` no longer claims
coverage from `multiconnection.sh`; it is explicitly marked as a required new
same-Target concurrent Login black-box case. Its 1 minute 2 second duration is
a cache-assisted quality retry, not an uncached deep-profile benchmark.

The current code is **not releasable as V3**. No push to `origin/feat` and no
goal completion is permitted until every `partial`, `blocked`, and `unverified`
row has direct current evidence, including the deployment-owned security and
external-Agent prerequisites.
