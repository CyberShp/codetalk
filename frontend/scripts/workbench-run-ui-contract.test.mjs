import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
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

test("run cockpit keeps event paging, SSE recovery, and pause history", () => {
  assert.match(runCockpitSource, /MAX_LOADED_EVENTS\s*=\s*2000/);
  assert.match(runCockpitSource, /tail:\s*true/);
  assert.match(runCockpitSource, /before_id/);
  assert.match(runCockpitSource, /stream\.onerror = \(\) => \{ void refresh\(true\); \};/);
  assert.match(runCockpitSource, /pauseBoundary/);
  assert.match(runCockpitSource, /frozenEvents/);
});

test("run cockpit surfaces the frozen Skill invocation and Judge state", () => {
  assert.match(runCockpitSource, /function SkillInvocationPanel/);
  assert.match(runCockpitSource, /aria-label="Skill invocation"/);
  assert.match(runCockpitSource, /skill_version_id/);
  assert.match(runCockpitSource, /skill_content_digest/);
  assert.match(runCockpitSource, /Judge/);
  assert.match(runCockpitSource, /selected_delivery_ids/);
});

test("failed-node retry creates a child attempt and executes the generic run runtime", () => {
  assert.match(runCockpitSource, /workbenchTasksApi\.createRun\(taskId,\s*runId\)/);
  assert.match(runCockpitSource, /api\.workbench\.taskRuns\.execute\(attempt\.task_run_id/);
  assert.doesNotMatch(runCockpitSource, /taskRuns\.prepare\(/);
  assert.doesNotMatch(runCockpitSource, /taskRuns\.run\(/);
});

test("the run cockpit panel suppresses horizontal page scrolling", () => {
  assert.match(
    globalStyles,
    /\.ct-workbench-shell \.ct-run-cockpit-panel\s*\{[\s\S]{0,260}overflow-x:\s*hidden/,
  );
});

test("task authoring and summaries do not coerce unsupported contract versions", () => {
  assert.doesNotMatch(taskWizardSource, /Number\(definition\.compiled_contract_version\)/);
  assert.doesNotMatch(taskDetailSource, /Number\(task\.workflow_version\?\.compiled_definition\?\.compiled_contract_version\)/);
  assert.match(taskWizardSource, /const isV3Contract = true/);
});
