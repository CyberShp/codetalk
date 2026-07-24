---
feature_ids: [workflow-productization-v3]
topics: [workflow, harness, quality, security, ui]
doc_kind: gap-matrix
created: 2026-07-23
---

# V3 Contract Gap Matrix

This matrix records the current implementation audit. It is a delivery checklist, not proof
that a requirement is complete; an item is only complete after its required real E2E evidence.

| Product contract | Current owner / evidence | Gap to V3 | Next verification |
| --- | --- | --- | --- |
| WorkflowVersion | `WorkflowVersionStore`, published definitions and compiled plans | Published definitions are immutable, but the Version/RunSnapshot boundary still has more than one serialized representation | Contract fixture with version/hash assertions |
| RunSnapshot | `WorkbenchTaskRunPreparer`, `run_snapshot_v3.json` | V3 now provides one immutable component index for workflow/input/profile/network/stage/artifact/capability/readiness/quality bytes, verified before execution. Existing task-bundle consumers remain as compatibility projections. | Browser run showing the V3 snapshot in diagnostics and a final release fixture |
| InputBinding | `workflow_graph.py`, input ingest, `input_consumption.py` and scoped Agent bundles | Strict data edges and Agent-scoped ledgers now record named input, user label, type, content hash, stage status/mode and produced artifact. External Agent runtimes still need to emit equivalent consumption events rather than relying on staged builtin events. | External-Agent multi-input E2E with document/MR consumption ledger |
| Provider execution | `AgentRunHarness`, CLI bridge and built-in model paths | Lifecycle events now have a normalized vocabulary, but the Facade is not yet the sole durable provider adapter boundary | Adapter conformance fixtures for Codex and built-in model |
| Readiness | external provider discovery | Probe and execution are not yet proven to use one immutable capability snapshot | shared-probe integration test |
| Test activity skills | staged execution plus `test_activity_contract.py` | Nine StageSpecs are frozen, but staged runtime execution does not yet project all progress, gates and local recovery through this one registry | StageSpec registry and per-stage artifact/gate tests |
| Artifact contract | V3 contract, Manifest, output schemas and report rendering | Profile-dependent layers are centrally declared and Manifest reads the frozen Contract; deterministic V3 report rendering and download E2E remain | ArtifactContract V3 fixtures and download E2E |
| Quality | source-driven judge, claim validators and `claim_evidence_ledger.json` | L1/L2 outcomes now share a ledger, but it is not yet the sole delivery gate or targeted-repair source | injected false-claim regressions |
| Zero Public Egress | network policy, model factory and Agent sandbox | Runtime admits only deployment-approved model hostnames plus declared model API routes; telemetry, trace, update, package registry and hosted MCP traffic are hard-denied. IP class is not used as the authority. Deployment traffic capture and certified firewall evidence remain | policy client, sandbox audit and traffic-capture tests |
| Cockpit / AI thread | `run-cockpit-page.tsx`, AI artifact references | Both consume task artifacts, but profile/status semantics and bounded presentation need V3 review | browser E2E, narrow-screen screenshots |

## Confirmed baseline observations

- Chromium Attempt 5 for the SPDK iSCSI deep workflow expanded the cockpit's
  `输入消费记录（3 项）` panel and showed the user label, type and six concrete
  stage artifacts for the analysis target, design document and source workspace.
  The recorded evidence is under
  `/Volumes/Media/codetalk-e2e-artifacts/v3-deepseek-flash-pro-risk-evidence-20260724/`.
  It proves the builtin staged path; it must not be generalized to external
  Agent consumption until that runtime emits the same event contract.

- Existing legacy presets did not declare execution profiles. The task wizard consequently
  hid policy selection until compatibility defaults were added. This is covered by the
  `test_prepare_legacy_workflow_allows_the_v3_deep_execution_profile` regression and a
  real browser journey.
- A workflow can already carry typed ports and strict single-edge validation, so V3 extends
  that contract instead of replacing it with an untyped graph.
- The present runtime is serial at compiled-plan level. Any durable stage runtime decision
  must be made after isolated, reproducible SDK POCs. Those POCs may download and evaluate
  candidate SDKs under the engineering/CI network policy. "Offline" is a deployment-runtime
  constraint only: a shipped CodeTalk instance must not let an SDK autonomously contact a
  vendor for updates, telemetry, traces, discovery, marketplaces, or package installation.
  No SDK is selected by this document.
