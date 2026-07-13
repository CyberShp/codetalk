import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "Workbench V2 task center real E2E",
});

test("filters a real Task, creates Attempt 2, and clones it without rewriting Attempt 1", async ({ page, request }) => {
  const stamp = Date.now();
  const workflowId = `task-center-e2e-${stamp}`;
  const taskName = `Task Center E2E ${stamp}`;
  const workspaceName = `Task Center Repo ${stamp}`;
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-task-center-")));
  fs.writeFileSync(path.join(repo, "README.md"), "# Task center real source\n", "utf8");
  execFileSync("git", ["init", "-q", repo]);

  const workspaceResponse = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResponse.ok()).toBeTruthy();
  const workspaceId = (await workspaceResponse.json()).id as string;
  const graph = starterGraph(workflowId, taskName);
  const workflowResponse = await request.post(`${backendBase}/api/workbench/workflows`, {
    data: { id: workflowId, name: taskName, description: "Task center E2E", authoring_graph: graph },
  });
  expect(workflowResponse.ok()).toBeTruthy();
  const draftId = (await workflowResponse.json()).current_draft_version_id as string;
  const publishResponse = await request.post(`${backendBase}/api/workbench/workflows/${workflowId}/versions/${draftId}/publish`, { data: {} });
  expect(publishResponse.ok()).toBeTruthy();
  const publishedId = (await publishResponse.json()).version_id as string;
  const taskResponse = await request.post(`${backendBase}/api/workbench/tasks`, {
    data: {
      name: taskName,
      description: "保留多次运行的真实任务",
      workspace_id: workspaceId,
      workflow_id: workflowId,
      workflow_version_id: publishedId,
      lifecycle_status: "ready",
      input_values: {},
      tags: ["e2e"],
    },
  });
  expect(taskResponse.ok()).toBeTruthy();
  const taskId = (await taskResponse.json()).task_id as string;
  const firstRun = await request.post(`${backendBase}/api/workbench/tasks/${taskId}/runs`, { data: {} });
  expect(firstRun.ok()).toBeTruthy();
  expect((await firstRun.json()).attempt_number).toBe(1);

  await page.goto("/tasks?execution_status=prepared", { waitUntil: "domcontentloaded" });
  const taskRow = page.getByRole("row").filter({ hasText: taskName });
  await expect(taskRow).toBeVisible();
  await page.getByLabel("生命周期").selectOption("ready");
  await expect(page).toHaveURL(/lifecycle_status=ready/);
  await taskRow.getByRole("link").first().click();
  await page.getByRole("button", { name: "运行记录" }).click();
  await expect(page.getByText("Attempt 1", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "启动新运行" }).hover();
  await page.getByRole("button", { name: "启动新运行" }).click();
  await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}/runs/task_run_`));
  await expect(page.getByText("Attempt 2", { exact: true })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("link", { name: "返回任务" }).click();
  await page.getByRole("button", { name: "运行记录" }).click();
  await expect(page.getByText("Attempt 1", { exact: true })).toBeVisible();

  await page.getByRole("link", { name: "任务中心" }).first().click();
  await page.getByRole("row").filter({ hasText: taskName }).getByRole("button", { name: "复制任务" }).click();
  await page.waitForURL(/\/tasks\/task_/);
  await expect(page.getByText("草稿", { exact: true })).toBeVisible();
  await expect(page.getByText(`${taskName} 副本`, { exact: true })).toBeVisible();
});

function starterGraph(workflowId: string, name: string) {
  return {
    schema_version: 2,
    workflow_id: workflowId,
    name,
    description: "",
    nodes: [
      { id: "repository", kind: "input", label: "源码工作区", position: { x: 80, y: 120 }, config: { contract_id: "repo_path", label: "源码工作区", type: "directory", required: true, resolver: "workspace", role: "源码目录" } },
      { id: "analyze", kind: "agent", label: "源码分析", position: { x: 380, y: 120 }, config: { step_id: "analyze", goal: "阅读源码并生成分析报告", provider: "builtin-llm", mcp_profiles: [], skill_ids: [], required_artifacts: ["report.md"], input_ports: [{ id: "repo_path", type: "directory", required: true }], output_ports: [{ id: "report", type: "markdown" }], timeout_sec: 900, idle_timeout_sec: 120, failure_policy: "stop" } },
      { id: "report", kind: "output", label: "分析报告", position: { x: 700, y: 120 }, config: { output_id: "report", label: "分析报告", type: "markdown", artifact: "report.md", required: true, source_node_id: "analyze", source_port_id: "report" } },
    ],
    edges: [
      { id: "repository-analyze", kind: "data", source: { node_id: "repository", port_id: "value" }, target: { node_id: "analyze", port_id: "repo_path" } },
      { id: "analyze-report", kind: "data", source: { node_id: "analyze", port_id: "report" }, target: { node_id: "report", port_id: "value" } },
    ],
    settings: { stop_on_error: true, max_parallelism: 1 },
  };
}
