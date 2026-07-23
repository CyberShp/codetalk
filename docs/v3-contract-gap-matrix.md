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
| RunSnapshot | `WorkbenchTaskRunPreparer`, `task_bundle.json` | Inputs, execution profile, network policy, StageSpec and Artifact Contract are now frozen; capability and Quality snapshots need a single explicit schema | RunSnapshot V3 schema test |
| InputBinding | `workflow_graph.py`, input ingest and scoped Agent bundles | Strict data edges and Agent-scoped input ledgers exist; named per-stage consumption is still incomplete | Multi-input E2E with document consumption ledger |
| Provider execution | `AgentRunHarness`, CLI bridge and built-in model paths | Lifecycle events now have a normalized vocabulary, but the Facade is not yet the sole durable provider adapter boundary | Adapter conformance fixtures for Codex and built-in model |
| Readiness | external provider discovery | Probe and execution are not yet proven to use one immutable capability snapshot | shared-probe integration test |
| Test activity skills | staged execution plus `test_activity_contract.py` | Nine StageSpecs are frozen, but staged runtime execution does not yet project all progress, gates and local recovery through this one registry | StageSpec registry and per-stage artifact/gate tests |
| Artifact contract | V3 contract, Manifest, output schemas and report rendering | Profile-dependent layers are centrally declared and Manifest reads the frozen Contract; deterministic V3 report rendering and download E2E remain | ArtifactContract V3 fixtures and download E2E |
| Quality | source-driven judge, claim validators and `claim_evidence_ledger.json` | L1/L2 outcomes now share a ledger, but it is not yet the sole delivery gate or targeted-repair source | injected false-claim regressions |
| Zero Public Egress | network policy, model factory and Agent sandbox | Runtime admits only deployment-approved model hostnames plus declared model API routes; telemetry, trace, update, package registry and hosted MCP traffic are hard-denied. IP class is not used as the authority. Deployment traffic capture and certified firewall evidence remain | policy client, sandbox audit and traffic-capture tests |
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
