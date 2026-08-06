# Skill single-step real Harness qualification

This pilot intentionally executes only the first topological Skill step. It is
not the complete nine-step/Judge implementation.

## Expected runtime path

`Task -> Run Attempt -> WorkbenchTaskRunPreparer -> agent_runs[0] -> WorkbenchWorkflowRunner -> AgentHarnessFacade -> OpenCode`

The production run must not enter `_execute_v3_skill_step_node` and must not
create a `fake-*` session.

## Intranet qualification

1. Configure the existing OpenCode runtime and its DeepSeek-compatible model.
2. Restart the backend so `app.main` installs the bounded bridge.
3. Create a new Task from a published Skill Version. Existing frozen attempts
   are intentionally unchanged.
4. Start one Run Attempt and wait for the first step to finish.
5. Inspect the Run directory.

Required evidence:

- `task_run.json.agent_runs` contains exactly one item for the first Skill step.
- Its `run_id` and provider session do not start with `fake-`.
- The provider is the configured OpenCode runtime.
- `agent_runs/<step-id>/agent_run.json` exists.
- `agent_runs/<step-id>/agent_run_lifecycle.json` contains real provider timing
  and a duration materially greater than the old scripted 10 ms path.
- Every path listed in that Agent Run's `required_artifacts` exists below its
  artifact directory.
- `workflow_execution.json.step_results[0].type` is `agent_task`.
- A missing required artifact produces `invalid`/`failed`, never `completed`.

Example checks from the backend data directory:

```bash
RUN=<task-run-directory>
jq '.agent_runs' "$RUN/task_run.json"
grep -R 'fake-' "$RUN" && echo 'FAIL: fake session found'
jq '.step_results[0] | {type,status,provider,execution,validation}' \
  "$RUN/workflow_execution.json"
find "$RUN/agent_runs" -maxdepth 4 -type f -print
```

## Current boundary

Only the first topological Skill step is compiled. The remaining steps,
cross-step artifact continuation, shared Producer session, run guard completion
gates, and isolated Judge remain blocked until this vertical path passes real
intranet qualification.
