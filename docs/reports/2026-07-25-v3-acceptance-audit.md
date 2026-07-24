---
feature_ids: [workflow-productization-v3]
topics: [acceptance, workflow, harness, quality, security]
doc_kind: acceptance-audit
created: 2026-07-25
---

# CodeTalk V3 Acceptance Audit

This is a completion audit, not a release claim.  A requirement is marked
`verified` only when the cited automated or browser evidence covers its full
scope.  `partial` and `missing` remain release blockers.

## Evidence Baseline

- Authority worktree: `/Volumes/Media/codetalk-v3-productization-resume`
- Audit head: `cf68ade7`
- Primary real-model evidence root:
  `/Volumes/Media/codetalk-e2e-artifacts/v3-deepseek-flash-pro-risk-evidence-20260724/`
- Successful browser quality-repair run:
  `task_run_219e4a93b89d464498e691e8cdc0ea94`
  (`completed`, `quality_status=passed`, no invalid final-acceptance checks).
- Attempt 7 (`task_run_834d592eecdf4533b610e0f32f4fa254`) exposed a real
  false positive: the SFMEA denylist read an error-code comparison ("session
  not found" versus "connection add failed") as a claim that no error exists.
  `cf68ade7` adds an explicit-risk-hypothesis exception plus a regression using
  that exact iSCSI row; generic normal-behavior and unproven-risk rejection
  regressions remain green.
- Fresh browser retry after that fix:
  `task_run_e1838bc1276f4f3cb0253389c5c94a62` completed in `5m02s` with
  `quality_status=passed`, `delivery_status=complete`, quality score `100`,
  zero issues, and final acceptance `46/46` required checks.  It used
  DeepSeek Flash for source/flow stages and DeepSeek Pro for SFMEA and
  black-box stages.  The profile disclosed one cross-run cached stage and
  targeted quality reuse, so it is a valid rapid browser completion but not an
  uncached performance baseline.

## Acceptance Status

| Area | Status | Evidence | Remaining condition |
| --- | --- | --- | --- |
| Workflow domain and typed ports | verified | WorkflowVersion/RunSnapshot tests; xyflow browser regressions; `workflow_snapshot.json` | Keep migration regression in release gate. |
| Node Registry and xyflow canvas | verified | Registry-driven inspector/browser evidence in V3 execution state; designer contract tests | Desktop and narrow-screen visual regression must be rerun at release. |
| Harness input, readiness and events | partial | Shared readiness, input snapshot, activity-aware timeout and Harness tests | Need two live executable providers under the same approved deployment policy. |
| Test Activity Skill Runtime | partial | Thirteen staged events, Flash/Pro routing and complete artifacts from Attempt 8 | Deep profile must complete with all gates and a separately audited sample. |
| Artifact Contract and Claim/Evidence | verified for the DeepSeek B rapid sample | Attempt 8: six downloadable deliverables, 48/48 verified facts, `46/46` final checks and zero audit issues | Repeat against deep profile and record Markdown/JSON hash audit. |
| Quality and targeted repair | verified for declared findings | `cf68ade7`, focused contract/task-run regressions, Attempt 8 browser completion | Independent accuracy review still must score the final deep sample. |
| AI-thread result linkage | partial | Existing V3 AI-thread browser regression recorded in execution state | Rerun against the final deep deliverable and save fresh screenshot/trace. |
| Zero Autonomous Egress code/process controls | partial | Network policy and sandbox regressions (`25 passed`); real macOS sandbox DNS denial | Need deployment-owned firewall/DNS capture covering both basic workflows and an allowed approved provider/MCP route. |
| SDK POC and runtime decision | partial | Isolated POC ADR and version/hash/license record | Complete comparison matrix, select or formally retain the existing durable runner, and record the decision. |
| E2E-A external Agent | blocked by environment, not passed | Settings-page Codex readiness correctly fail-closed without an auditable Agent egress gateway | Deployment administrator must provide approved Agent route and captured traffic; no bypass or silent builtin fallback is acceptable. |
| E2E-B builtin model | verified for rapid/retry chain | Browser-created task, real uploaded design document, DeepSeek Flash + Pro events, six deliverables, quality pass; Attempt 8 completed in `5m02s` | Complete a fresh deep run on the same published workflow. |
| Performance and reliability | partial | Stage metrics and cache/reuse disclosure exist | Collect five-run P50/P95 for rapid; complete deep run; test restart, concurrent tasks and long-result rendering. |
| UI and documentation | partial | V3 UI browser evidence and current guides | Capture final desktop/mobile evidence and update the operator-facing manual with final semantics. |

## Release Blockers

1. A live external Agent cannot be claimed until its approved egress gateway exists and
   readiness, run, input consumption, artifact collection and captured traffic all pass.
2. One deep (`45-90` minute target) SPDK sample must complete from the published
   workflow without substituting the rapid profile or a cached result.
3. Deployment traffic capture must prove that only user-triggered Provider Adapter
   inference reaches an approved endpoint; trace, telemetry, updates, package sources,
   hosted MCP and Agent bypasses must remain absent.
4. An independent reviewer/model must score the deep artifacts at least 80 and must
   inspect factual claims rather than only report formatting.
5. Release evidence still needs the full AC matrix, final desktop/mobile screenshots,
   restart/concurrency evidence, and an updated operator manual.

## Non-Claims

- A short cache-assisted retry is not a rapid performance baseline.
- A completed Stage is not a deliverable until final acceptance passes.
- A local CLI command that exists is not an executable Agent in intranet mode.
- A code-level denylist is not deployment traffic-capture evidence.
