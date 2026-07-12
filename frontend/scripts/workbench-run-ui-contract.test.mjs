import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
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
    source,
    /generateTaskAcceptanceAudit[\s\S]{0,1200}acceptanceAudit\([\s\S]{0,1200}restoreTaskRun\(/,
  );
});

test("corrupt workflow execution does not block restoring the acceptance audit", () => {
  assert.match(
    source,
    /workflow_execution\.json[\s\S]{0,900}try\s*\{[\s\S]{0,900}JSON\.parse[\s\S]{0,900}catch[\s\S]{0,2200}task_acceptance_audit\.json/,
  );
});
