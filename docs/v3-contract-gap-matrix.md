---
feature_ids: [workflow-productization-v3]
topics: [workflow, harness, quality, security, ui]
doc_kind: gap-matrix
created: 2026-07-23
---

# V3 Contract Gap Matrix

This matrix records the Phase 0 baseline. It is a delivery checklist, not proof that a
requirement is complete.

| Product contract | Current owner / evidence | Gap to V3 | Next verification |
| --- | --- | --- | --- |
| WorkflowVersion | `WorkflowVersionStore`, published definitions and compiled plans | Version exists, but capability, network, Artifact and Quality snapshots are not one explicit V3 contract | Contract fixture with version/hash assertions |
| RunSnapshot | `WorkbenchTaskRunPreparer`, `task_bundle.json` | Inputs and workflow are frozen; execution profile is now frozen, but network/capability/stage budgets are absent | RunSnapshot V3 schema test |
| InputBinding | `workflow_graph.py`, input ingest and scoped Agent bundles | Strict data edges exist; consumption accounting and named Stage consumption are incomplete | Multi-input E2E with document consumption ledger |
| Provider execution | `AgentRunHarness`, CLI bridge and built-in model paths | Provider lifecycle and diagnostics are provider-shaped rather than a durable facade event vocabulary | Adapter conformance fixtures for Codex and built-in model |
| Readiness | external provider discovery | Probe and execution are not yet proven to use one immutable capability snapshot | shared-probe integration test |
| Test activity skills | staged execution plus `test_activity_contract.py` | Behavior is rich but not represented as nine observable StageSpec contracts | StageSpec registry and per-stage artifact/gate tests |
| Artifact contract | manifest, output schemas, report rendering | Outputs exist, but deliverable/supporting/diagnostic and profile-dependent requirements are not centrally declared | ArtifactContract V3 fixtures and download E2E |
| Quality | source-driven judge and claim validators | Existing validators are not yet a unified Claim/Evidence ledger with targeted repair inputs | injected false-claim regressions |
| Zero Public Egress | local clients and optional Agent sandbox | No centralized allow-list transport or immutable network policy snapshot; default Agent setting currently permits network | policy client, sandbox audit and traffic-capture tests |
| Cockpit / AI thread | `run-cockpit-page.tsx`, AI artifact references | Both consume task artifacts, but profile/status semantics and bounded presentation need V3 review | browser E2E, narrow-screen screenshots |

## Confirmed baseline observations

- Existing legacy presets did not declare execution profiles. The task wizard consequently
  hid policy selection until compatibility defaults were added. This is covered by the
  `test_prepare_legacy_workflow_allows_the_v3_deep_execution_profile` regression and a
  real browser journey.
- A workflow can already carry typed ports and strict single-edge validation, so V3 extends
  that contract instead of replacing it with an untyped graph.
- The present runtime is serial at compiled-plan level. Any durable stage runtime decision
  must be made only after isolated offline POCs; no SDK is selected by this document.

