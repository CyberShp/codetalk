import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const controllerSource = readFileSync(
  join(root, "../src/app/workbench/workbench-controller.ts"),
  "utf8",
);
const globalStyles = readFileSync(join(root, "../src/app/globals.css"), "utf8");
const runCockpitSource = readFileSync(
  join(root, "../src/features/runs/run-cockpit-page.tsx"),
  "utf8",
);
const taskWizardSource = readFileSync(
  join(root, "../src/features/tasks/task-wizard.tsx"),
  "utf8",
);
const taskDetailSource = readFileSync(
  join(root, "../src/features/tasks/workbench-task-detail-page.tsx"),
  "utf8",
);
const source = ["run-view.tsx", "workbench-controller.ts", "workbench-shared.tsx"]
  .map((name) =>
    readFileSync(join(root, "../src/app/workbench", name), "utf8"),
  )
  .join("\n");

test("workbench cockpit treats weak-success states as review, not normal running", () => {
  assert.match(source, /completed_empty/);
  assert.match(source, /needs_review/);
  assert.match(source, /完成但信息不足/);
  assert.match(source, /需要复核/);
  assert.match(source, /runPanelStatus[\s\S]*验收提醒/);
});

test("workbench cockpit renders restart and review task-run events in Chinese", () => {
  assert.match(source, /interrupted:\s*["']运行中断["']/);
  assert.match(source, /needs_review:\s*["']需要复核["']/);
  assert.match(source, /completed_empty:\s*["']完成但信息不足["']/);
});

test("restoring a public task run keeps the executable workspace path", () => {
  assert.doesNotMatch(source, /setRepoPath\(run\.repo_path\)/);
  assert.match(source, /availableWorkspaces\.find\([\s\S]*run\.workspace_id/);
  assert.match(source, /restoredWorkspace[\s\S]*repo_path/);
  assert.match(source, /restoreTaskRun\(recoverableRun!?\.task_run_id, visibleWorkspaces\)/);
  assert.doesNotMatch(source, /setWorkspaces\(visibleWorkspaces\)[\s\S]{0,2500}restoreTaskRun\(recoverableRun\.task_run_id\)(?!,)/);
});

test("workbench renders the built-in workflow provider as a user-facing model label", () => {
  assert.match(source, /["']builtin-llm["']:\s*["']内置模型["']/);
  assert.match(source, /执行器:\s*\{providerDisplayLabel\(selectedRunProvider\)\}/);
});

test("acceptance audit refreshes the task quality panel from persisted execution", () => {
  assert.match(
    controllerSource,
    /generateTaskAcceptanceAudit[\s\S]{0,1200}acceptanceAudit\([\s\S]{0,1200}restoreTaskRun\(/,
  );
});

test("corrupt workflow execution does not block restoring the acceptance audit", () => {
  assert.match(
    controllerSource,
    /workflow_execution\.json[\s\S]{0,900}try\s*\{[\s\S]{0,900}JSON\.parse[\s\S]{0,900}catch[\s\S]{0,2200}task_acceptance_audit\.json/,
  );
});

test("failed-node retry uses the validated rerun-plan endpoint", () => {
  assert.match(
    controllerSource,
    /executeTaskRerunPlan[\s\S]{0,900}markTaskRunSubmitted\(taskRun, \{ startPolling: false \}\)[\s\S]{0,400}const rerunRequest = api\.workbench\.taskRuns\.executeRerunPlan\([\s\S]{0,600}startTaskRunPolling\(taskRun\.task_run_id, \{[\s\S]{0,260}requireActivity: true[\s\S]{0,260}await rerunRequest/,
  );
  assert.match(
    controllerSource,
    /executeTaskRerunPlan[\s\S]{0,900}taskRuns\.executeRerunPlan\(/,
  );
  assert.doesNotMatch(
    controllerSource,
    /executeTaskRerunPlan[\s\S]{0,900}taskRuns\.execute\(/,
  );
});

test("an active node contributes visible in-flight progress", () => {
  assert.match(source, /activeProgressCredit\s*=\s*runningIndex\s*>=\s*0\s*\?\s*0\.5\s*:\s*0/);
  assert.match(source, /completed\s*\+\s*activeProgressCredit/);
});

test("capability panel does not claim ready while MCP or executor warnings exist", () => {
  assert.match(source, /runPanelCapabilitySummary\.warnings\.length\s*>\s*0\s*\?\s*["']降级可用["']/);
});

test("cancelled runs hide stale quality and acceptance warnings", () => {
  assert.match(source, /runIsCancelled[\s\S]{0,300}visibleTaskAcceptanceAudit/);
  assert.match(source, /const testActivityQuality = runIsCancelled[\s\S]{0,120}undefined/);
  assert.match(source, /runPanelFailureReasons[\s\S]{0,150}if \(runIsCancelled\) return \[\]/);
});

test("background task settlement refreshes artifacts without overwriting the form draft", () => {
  assert.match(
    source,
    /pollTaskRunUntilSettled[\s\S]{0,2200}restoreTaskRun\(taskRunId,\s*workspaces,\s*\{[\s\S]{0,160}preserveDraft: true,[\s\S]{0,160}pollGeneration: generation/,
  );
  assert.match(source, /if \(!options\.preserveDraft\)[\s\S]{0,900}setProviderOverride/);
});

test("polling uses a generation token so stale requests cannot overwrite a newer run", () => {
  assert.match(controllerSource, /taskRunPollingGenerationRef\s*=\s*useRef\(0\)/);
  assert.match(controllerSource, /ownsTaskRunPolling\(taskRunId, generation\)/);
  assert.match(
    controllerSource,
    /refreshTaskRunRuntime[\s\S]{0,900}ownsTaskRunPolling\(taskRunId, generation\)[\s\S]{0,180}owned: false/,
  );
  assert.match(controllerSource, /function startTaskRunPolling\([\s\S]{0,400}restart\?: boolean/);
  assert.match(controllerSource, /taskRunEventSourceRef\.current === source/);
  assert.doesNotMatch(
    controllerSource,
    /executePreparedWorkflow[\s\S]{0,900}markTaskRunSubmitted\(taskRun\)[\s\S]{0,900}startTaskRunPolling\(taskRun\.task_run_id\)/,
  );
});

test("rerun completion reports the actual terminal outcome and reloads history", () => {
  assert.match(controllerSource, /taskRerunSettledMessage\(result/);
  assert.match(controllerSource, /rerunHistory\(taskRun\.task_run_id\)/);
  assert.doesNotMatch(controllerSource, /已从失败节点完成复跑/);
});

test("rerun starts after the backend event tail instead of a truncated local page", () => {
  assert.match(controllerSource, /latest_event_id/);
  assert.match(
    controllerSource,
    /executeTaskRerunPlan[\s\S]{0,900}taskRuns\.events\([\s\S]{0,400}latest_event_id[\s\S]{0,1000}afterEventId/,
  );
});

test("stale restore failures cannot clear a newer task after generation changes", () => {
  assert.match(
    controllerSource,
    /catch\s*\{[\s\S]{0,300}pollGeneration[\s\S]{0,300}ownsTaskRunPolling[\s\S]{0,200}setWorkflowExecution\(null\)/,
  );
});

test("the sticky run console is enabled only in the two-column desktop layout", () => {
  const baseRule = globalStyles.match(
    /\.ct-workbench-shell \.ct-run-console\s*\{([^}]*)\}/,
  );
  assert.ok(baseRule);
  assert.doesNotMatch(baseRule[1], /position:\s*sticky/);
  assert.match(
    globalStyles,
    /\.ct-workbench-shell \.ct-run-console\s*\{[\s\S]{0,180}overflow:\s*auto[\s\S]{0,260}@media \(min-width:\s*1280px\)[\s\S]{0,220}\.ct-workbench-shell \.ct-run-console\s*\{[\s\S]{0,100}position:\s*sticky/,
  );
});

test("the run cockpit panel suppresses horizontal page scrolling", () => {
  assert.match(source, /ct-run-cockpit-panel/);
  assert.match(
    globalStyles,
    /\.ct-workbench-shell \.ct-run-cockpit-panel\s*\{[\s\S]{0,100}overflow-x:\s*hidden/,
  );
});

test("the V3 cockpit rejects unsupported frozen contract versions without numeric coercion", () => {
  assert.doesNotMatch(runCockpitSource, /Number\(record\.compiled_contract_version/);
  assert.match(runCockpitSource, /typeof version !== "number" \|\| version !== 3/);
  assert.match(runCockpitSource, /冻结契约版本不受支持/);
});

test("task authoring and summaries do not coerce unsupported contract versions into V3", () => {
  assert.doesNotMatch(taskWizardSource, /Number\(definition\.compiled_contract_version\)/);
  assert.doesNotMatch(taskDetailSource, /Number\(task\.workflow_version\?\.compiled_definition\?\.compiled_contract_version\)/);
  assert.match(taskWizardSource, /definition\.compiled_contract_version === 3/);
  assert.match(taskDetailSource, /compiled_contract_version === 3/);
});
