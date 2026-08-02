import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const taskWizard = readFileSync(
  join(root, "../src/features/tasks/task-wizard.tsx"),
  "utf8",
);
const taskApi = readFileSync(
  join(root, "../src/lib/api/workbench-tasks.ts"),
  "utf8",
);
const runCockpit = readFileSync(
  join(root, "../src/features/runs/run-cockpit-page.tsx"),
  "utf8",
);
const workspacePage = readFileSync(
  join(root, "../src/app/workspaces/[id]/page.tsx"),
  "utf8",
);

test("Phase2 task inputs show workflow examples and missing guidance", () => {
  assert.match(taskWizard, /item\.example/);
  assert.match(taskWizard, /item\.missing_guidance/);
  assert.match(taskWizard, /ct-v2-input-guidance/);
});

test("Phase2 workflow selection stays compact until all scenarios are requested", () => {
  assert.match(taskWizard, /CORE_WORKFLOW_IDS/);
  assert.match(taskWizard, /常用工作流/);
  assert.match(taskWizard, /全部场景/);
  assert.match(taskWizard, /item\.id === value/);
});

test("Phase2 retries reuse the parent attempt input snapshot", () => {
  assert.match(taskApi, /parent_task_run_id:\s*parentTaskRunId/);
  assert.match(runCockpit, /createRun\(taskId,\s*runId/);
  assert.match(runCockpit, /从失败节点重试/);
});

test("Phase2 run cockpit explains waiting and failure recovery states", () => {
  assert.match(runCockpit, /waiting_for_input/);
  assert.match(runCockpit, /FailurePanel/);
  assert.match(runCockpit, /推荐操作/);
  assert.match(runCockpit, /检查执行器设置/);
});

test("workspace report shortcuts enter the Phase2 task wizard", () => {
  assert.match(workspacePage, /\/tasks\/new\?/);
  assert.match(workspacePage, /source_flow_sfmea_blackbox/);
  assert.match(workspacePage, /coverage_gap/);
  assert.doesNotMatch(workspacePage, /\/workbench\?/);
});

test("Phase2 task wizard consumes workflow, workspace, and target shortcuts", () => {
  assert.match(taskWizard, /params\.get\("workflow_id"\)/);
  assert.match(taskWizard, /params\.get\("workspace_id"\)/);
  assert.match(taskWizard, /params\.get\("target"\)/);
  assert.match(taskWizard, /"test_goal"/);
  assert.match(taskWizard, /"analysis_object"/);
});
