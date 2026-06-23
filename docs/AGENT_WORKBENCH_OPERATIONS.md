# Agent Workbench Operations Guide

This guide is the operational contract for the Agent Workbench, custom workflows,
Evidence Memory, and Agent Run Harness. It is meant for intranet deployment,
debugging, and acceptance testing after CodeTalk has been configured as the UI,
workflow, memory, validation, and audit layer.

## Progress State

Current implementation coverage is about 75% to 80% of the target design.

Implemented and verified:

- provider capability matrix and readiness artifacts;
- `ccr code`, Claude Code, OpenCode, and custom provider command parsing;
- workflow presets and editable workflow contracts;
- task preparation, execution, rerun, restore, and artifact manifest APIs;
- Agent-owned MCP request boundaries;
- Evidence Memory source slices, integrity status, and source-read discipline;
- semantic test-library import and workflow-output import;
- multi-turn Agent source-slice requests;
- frontend rendering for provider readiness, artifact validation, source slices,
  semantic import, rerun plans, and recent task restore.

Remaining before calling the goal complete:

- run an end-to-end intranet acceptance pass with a real `ccr code` or internal
  Agent CLI;
- verify provider startup probes on the target machine, not only with mocked
  commands;
- confirm GitNexus and CGC degraded paths are visible for the user's deployment;
- run the final backend/frontend regression matrix and record the exact results;
- close any issues found by that final E2E pass.

## Responsibility Boundary

CodeTalk owns deterministic control:

- workspace creation and repository path selection;
- workflow definitions, presets, custom inputs, and output schemas;
- task bundles and artifact declarations;
- provider capability/readiness reports;
- Evidence Memory, source slices, semantic library, and retrieval artifacts;
- local validation of repo paths, source files, symbols, coverage entries,
  artifact JSON schemas, sha256 values, and accepted/rejected evidence;
- UI display and audit trail.

Agent CLIs own credentialed exploration:

- `ccr code`, Claude Code, OpenCode, or internal Agent CLIs;
- private MCP credentials such as CodeHub MR access;
- MCP tools that only exist inside the Agent runtime;
- source exploration and reasoning inside the sandbox or account configured for
  that CLI.

CodeTalk does not trust Agent conclusions directly. Agent output becomes
evidence only after CodeTalk validates local paths, schemas, source slices, and
hashes.

## Provider Configuration

The default Claude provider command is:

```text
claude_code_command = "ccr code"
```

Environment override:

```text
CLAUDE_CODE_COMMAND=ccr code
```

OpenCode uses:

```text
opencode_command = "opencode"
```

Custom internal Agent CLIs should be configured through
`external_agent_custom_providers`. A provider must declare at least:

```json
{
  "id": "internal-agent",
  "label": "Internal Agent",
  "command": "internal-agent analyze",
  "prompt_transport": "stdin",
  "enabled": true
}
```

Commands with spaces, such as `ccr code`, are parsed as an argv vector. CodeTalk
must treat them as `["ccr", "code"]`, not as a single executable name.

## How CodeTalk Calls an Agent

For each Agent workflow step, CodeTalk:

1. prepares `task_bundle.json` with workflow inputs, repo instructions,
   provider readiness, Evidence Memory hits, source slices, semantic terms, and
   expected artifacts;
2. writes the Agent execution input and task bundle under the task artifact
   directory;
3. launches the configured command as a child process with the task bundle on
   stdin when the provider uses `prompt_transport=stdin`;
4. sets readonly environment hints such as `CODETALK_AGENT_READONLY=1` and the
   repository path;
5. captures stdout, stderr, exit status, timeout status, and raw output;
6. validates expected artifacts written by the Agent;
7. materializes only accepted artifacts into workflow outputs and Evidence
   Memory.

Every `agent_run.json` and `execution_input.json` carries `session_policy`.
The default policy is `external_session_mode=disposable_process`,
`resume_supported=false`, and `continuity_owner=codetalk_task_bundle`. This is
intentional: CodeTalk does not depend on a hidden long-lived Agent session.
Continuity comes from the task bundle, Evidence Memory, validated artifacts, and
sha256-backed source slices. Raw Agent output is never reused as memory unless it
has been converted into validated facts.

The first supported transport is stdin. If an Agent CLI does not support stdin,
it should be registered with a provider-specific wrapper or marked unavailable
with a diagnostic reason. CodeTalk should not silently pretend the Agent ran.

## Provider Readiness

Every prepared task writes:

```text
provider_readiness.json
provider_snapshot.json
agent_mcp_requests.json
```

Use these files to answer:

- Did CodeTalk find the command?
- Did the startup probe launch?
- Is the provider blocked, degraded, or ready?
- Is a missing capability CodeTalk-callable, Agent-owned, or unavailable?
- Are MR links or private MCP calls expected to be handled by the Agent CLI?

When all Agents are `unavailable`, check in this order:

1. `provider_readiness.json` for `command_found`, `startup_probe`, and
   `diagnostic`.
2. The configured command value, especially `CLAUDE_CODE_COMMAND`.
3. Whether `ccr` or the internal CLI is visible to the backend process PATH, not
   only to an interactive PowerShell window.
4. Whether the provider supports stdin or needs a wrapper command.
5. Whether antivirus, shell policy, or intranet endpoint policy prevents child
   process launch.
6. Raw Agent stdout/stderr artifacts under `agent_runs/<step>/turns/<turn>/`.

Missing GitNexus, CGC, or fast-context must be non-blocking when local source and
Agent CLI paths can continue. Missing repository source is blocking.

## GitNexus, CGC, and Fast-Context Degraded Mode

GitNexus and CGC are navigation and enrichment providers, not final authority.
If they are unavailable, the task should continue through local search,
Evidence Memory, semantic library, and Agent CLI exploration where possible.

The root `AGENTS.md` may require `mcp__fast-context__fast_context_search` for
exploratory code understanding. CodeTalk handles that rule as follows:

- if the backend can call fast-context directly, it uses it in the first
  discovery stage;
- if the backend cannot call it, it records a degraded decision instead of
  ignoring the instruction;
- if the external Agent CLI may have its own MCP credentials, the task bundle
  marks fast-context or CodeHub-style MCP access as `agent_owned_possible`;
- any result from fast-context or Agent-owned MCP is still only a candidate until
  local source validation succeeds.

Expected degraded artifacts include:

```text
context_discovery_decision.json
degraded_retrieval.json
provider_readiness.json
agent_mcp_requests.json
```

## Workflow Inputs

Workflows may define:

- plain text inputs, such as analysis object or risk type;
- file inputs, such as requirement documents, design documents, coverage reports,
  patch plans, patches, and diffs;
- link inputs, such as MR links;
- semantic-library inputs, such as existing feature test cases;
- Agent-owned MCP hints, such as CodeHub MR access.

For MR links and other private systems:

1. CodeTalk records the link as structured input.
2. The Agent task bundle tells the Agent which MCP capability may be needed.
3. The Agent CLI fetches through its own credentials.
4. The Agent returns artifacts such as `mr_summary.json`, `changed_files.json`,
   or `patch_diff.json`.
5. CodeTalk validates referenced repo files or patch snapshots before evidence
   materialization.

CodeTalk should not try to fetch private MR data if the credential boundary
belongs to the Agent CLI.

## Workflow Output Contract

Custom workflow outputs can be declared with compact syntax:

```text
id:type@resolver
id:type=artifact
```

Examples:

```text
source_scope:json=source_scope.json
black_box_cases:json=black_box_cases.json
report:markdown@report_render
```

Required outputs are complete only when their artifacts pass validation. An
Agent process exit code of 0 is not enough.

Key output and validation artifacts:

```text
workflow_contract.json
workflow_outputs.json
workflow_output_materialization.json
evidence_validation.json
task_artifact_manifest.json
```

## Evidence Memory Rules

Evidence Memory stores structured facts, not free-text memory.

Accepted facts may include:

- validated source files and symbols;
- source slices with repo-relative path, line range, sha256, and excerpt;
- accepted evidence cards;
- verified changed files;
- verified coverage gaps;
- semantic test-library references;
- accepted workflow artifacts.

Rejected facts must keep a reason, such as:

```text
file_not_found
outside_repo
non_source_file
schema_invalid
hash_mismatch
source_slice_stale
entry_unverified
```

Search hits and semantic-library hits are navigation signals. They become source
evidence only after CodeTalk attaches a valid source slice or accepted artifact.

## Multi-Turn Source Slice Flow

An Agent may ask for more code by writing `source_slice_requests.json`:

```json
{
  "need_source_slices": [
    {
      "file_path": "src/tls.c",
      "symbol": "nvmf_tcp_tls_handshake",
      "reason": "Need caller and registration context"
    }
  ]
}
```

CodeTalk then:

1. rejects paths outside the repo or non-source files;
2. reads bounded source slices from repo-local files;
3. writes `source_slices.json` with sha256-backed excerpts;
4. injects `requested_source_slices` into the next task bundle;
5. starts the next Agent turn;
6. stores each turn separately under `agent_runs/<step>/turns/<turn_id>/`.

Raw first-turn prose is not memory. Only validated slices and facts are passed
forward.

## Coverage and Black-Box Recommendations

Coverage analysis enters the Agent flow after uncovered functions are parsed.
For each high-risk uncovered function, CodeTalk can ask the Agent for external
entries such as RPC, API, CLI, config, message, timer, or callback triggers.

Rules:

- deterministic tracer entries are preserved;
- verified Agent entries are appended to `entry_paths`;
- unverified Agent entries remain candidate entries only;
- black-box cases are generated from verified entry paths and semantic-library
  terminology;
- Evidence Memory source slices may enrich case wording, but do not replace
  source validation.

Important artifacts:

```text
coverage_external_agent_discovery.json
coverage_entry_discovery.json
agent_discovery_session.json
agent_discovery_ledger.json
```

## Artifact Checklist

A healthy Workbench task should make these questions answerable from artifacts:

- What did the user ask for?
- Which workflow version and output schema were used?
- Which provider commands were configured?
- Which providers were available, degraded, or unavailable?
- Which MCP calls are CodeTalk-owned and which are Agent-owned?
- What exact task bundle did the Agent receive?
- What did each Agent turn return?
- Which artifacts were accepted or rejected?
- Which source files and line ranges were read?
- Which sha256 values prove the source slice contents?
- Why was a second turn triggered or skipped?
- What can be rerun without changing inputs?

Core artifact names:

```text
task_run.json
input_snapshot.json
workflow_snapshot.json
workflow_contract.json
task_bundle.json
agent_instructions.json
context_discovery_decision.json
provider_snapshot.json
provider_readiness.json
agent_mcp_requests.json
memory_retrieval.json
source_read_chain.json
evidence_consumption_trajectory.json
degraded_retrieval.json
agent_runs/<step>/execution_input.json
agent_runs/<step>/task_bundle.json
agent_runs/<step>/agent_run.json
agent_runs/<step>/raw_stdout.txt
agent_runs/<step>/raw_stderr.txt
agent_runs/<step>/result.json
agent_runs/<step>/turns/<turn_id>/*.json
evidence_validation.json
workflow_outputs.json
workflow_output_materialization.json
semantic_output_import.json
task_rerun_plan.json
task_rerun_execution.json
task_rerun_history.json
task_artifact_manifest.json
```

## Intranet Acceptance Checklist

Before marking an intranet deployment healthy:

1. Create a workspace from `frontend`, `nof`, and `nvmf_tcp` roots if the repo
   layout requires all three entry points.
2. Run source discovery for `nvme-tcp-tls`.
3. Confirm query expansion reaches `nvmf_tcp`, `transport/tls`, and
   `nvmf_tcp/transport/tls`.
4. Confirm Agent round 1 starts even when local search or GitNexus returns
   candidates.
5. Confirm `provider_readiness.json` explains every unavailable provider.
6. Confirm `ccr code` or the internal Agent CLI is visible to the backend
   process.
7. Confirm task bundles include repo `AGENTS.md` instructions.
8. Confirm Agent-owned MCP requests are recorded when MR links are used.
9. Confirm source candidates are accepted only after local source validation.
10. Run coverage analysis and confirm verified Agent entries can create
    black-box-ready gaps.
11. Confirm unverified entries stay candidate-only.
12. Confirm black-box case wording can use semantic-library terms.
13. Confirm rerun plan exists after provider or artifact failure.
14. Restore a recent task and confirm artifacts, materialization, semantic
    import, and rerun state are still visible.

## Regression Commands

Focused backend verification:

```powershell
cd backend
python -m pytest tests/test_external_agent_discovery.py -q
python -m pytest tests/test_agent_workbench_api.py tests/test_workbench_task_run.py tests/test_workflow_presets.py tests/test_agent_discovery_session.py tests/test_context_discovery.py -q
```

Frontend verification:

```powershell
cd frontend
npm run lint
```

A full backend pytest run is still useful before release, but the current suite
may exceed a short local timeout. Record the exact timeout or completion result
instead of treating an interrupted run as a pass.

## Known Residual Risk

The current Agent integration still uses prompt-level readonly rules, process
timeouts, command diagnostics, and local validation. It does not provide an OS
level sandbox for the external Agent CLI. Use trusted internal machines and
trusted provider commands until a real command proxy or sandbox is added.
