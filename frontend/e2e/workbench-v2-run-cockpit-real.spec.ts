import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

assertCanMutatePublicRuntime({ env: process.env, flowName: "Workbench V2 real run cockpit" });

test("starts a real Attempt and opens the bounded live run cockpit", async ({ page, request }) => {
  const stamp = Date.now();
  const workflowId = `run-cockpit-e2e-${stamp}`;
  const workflowName = `运行驾驶舱 E2E ${stamp}`;
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-run-cockpit-")));
  fs.writeFileSync(path.join(repo, "README.md"), "# Real cockpit source\n", "utf8");
  execFileSync("git", ["init", "-q", repo]);

  const workspaceResponse = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workflowName, repo_path: repo },
  });
  expect(workspaceResponse.ok()).toBeTruthy();
  const workspaceId = (await workspaceResponse.json()).id as string;
  const workflowResponse = await request.post(`${backendBase}/api/workbench/workflows`, {
    data: {
      id: workflowId,
      name: workflowName,
      description: "真实运行驾驶舱回归",
      authoring_graph: cockpitGraph(workflowId, workflowName),
    },
  });
  expect(workflowResponse.ok()).toBeTruthy();
  const draftId = (await workflowResponse.json()).current_draft_version_id as string;
  const publishResponse = await request.post(
    `${backendBase}/api/workbench/workflows/${workflowId}/versions/${draftId}/publish`,
    { data: {} },
  );
  expect(publishResponse.ok()).toBeTruthy();
  const versionId = (await publishResponse.json()).version_id as string;
  const taskResponse = await request.post(`${backendBase}/api/workbench/tasks`, {
    data: {
      name: workflowName,
      description: "从任务详情真实启动并观察事件",
      workspace_id: workspaceId,
      workflow_id: workflowId,
      workflow_version_id: versionId,
      lifecycle_status: "ready",
      input_values: { analysis_target: "逐字读取输入并检查 README 源码证据" },
      tags: ["e2e", "cockpit"],
    },
  });
  expect(taskResponse.ok()).toBeTruthy();
  const taskId = (await taskResponse.json()).task_id as string;

  await page.goto(`/tasks/${taskId}`, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "启动新运行" }).hover();
  await page.getByRole("button", { name: "启动新运行" }).click();

  await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}/runs/task_run_`));
  await expect(page.getByRole("heading", { name: workflowName })).toBeVisible();
  await expect(page.getByText("Attempt 1", { exact: true })).toBeVisible();
  await expect(page.getByText("执行状态", { exact: true })).toBeVisible();
  await expect(page.getByText("质量状态", { exact: true })).toBeVisible();
  await expect(page.getByText("交付状态", { exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "实时输出" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "当前节点" })).toBeVisible();
  await expect(page.locator(".ct-v2-run-cockpit")).toHaveCSS("height", /\d+px/);
  await page.getByRole("tab", { name: "全部事件" }).click();
  await expect(page.locator(".ct-v2-event-row").first()).toBeVisible({ timeout: 30_000 });
  const visibleEventCount = await page.locator(".ct-v2-event-row").count();
  await page.getByRole("button", { name: "暂停" }).click();
  await expect(page.getByText("显示已冻结在当前时刻，后台运行不受影响。")).toBeVisible();
  await expect(page.locator(".ct-v2-event-row")).toHaveCount(visibleEventCount);
  await page.getByRole("button", { name: "继续" }).click();
  await expect(page.getByRole("button", { name: "技术诊断" })).toBeVisible();
  await expect(page.locator(".ct-v2-diagnostic-drawer")).toBeHidden();
  await page.getByRole("button", { name: "技术诊断" }).click();
  await expect(page.locator(".ct-v2-diagnostic-drawer")).toBeVisible();
  await expect(page.getByRole("link", { name: "下载脱敏诊断包" })).toBeVisible();
  await page.getByRole("button", { name: "关闭技术诊断" }).click();
  await expect(page.locator(".ct-v2-diagnostic-drawer")).toBeHidden();

  const desktopEvidence = path.join(process.cwd(), "output", "playwright", "phase6", "run-cockpit-desktop.png");
  fs.mkdirSync(path.dirname(desktopEvidence), { recursive: true });
  await page.screenshot({ path: desktopEvidence, fullPage: false });
  const desktopBounds = await page.locator(".ct-v2-run-cockpit").boundingBox();
  expect(desktopBounds?.y ?? 0).toBeGreaterThanOrEqual(0);
  expect((desktopBounds?.y ?? 0) + (desktopBounds?.height ?? 0)).toBeLessThanOrEqual(900);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByText("执行状态", { exact: true })).toBeVisible();
  const horizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(horizontalOverflow).toBeLessThanOrEqual(1);
  await page.screenshot({ path: path.join(path.dirname(desktopEvidence), "run-cockpit-mobile.png"), fullPage: false });

  const failedRunId = page.url().split("/").pop() as string;
  await page.getByRole("button", { name: "从失败节点重试" }).click();
  await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}/runs/task_run_`));
  await expect(page.getByText("Attempt 2", { exact: true })).toBeVisible({ timeout: 30_000 });
  const childRunId = page.url().split("/").pop() as string;
  expect(childRunId).not.toBe(failedRunId);
  const childResponse = await request.get(`${backendBase}/api/workbench/task-runs/${childRunId}`);
  expect(childResponse.ok()).toBeTruthy();
  const child = await childResponse.json();
  expect(child.parent_task_run_id).toBe(failedRunId);
  expect(child.task_bundle.retry_source.task_run_id).toBe(failedRunId);
  expect(child.task_bundle.retry_source.mode).toBe("from_failed_node");
});

function cockpitGraph(workflowId: string, name: string) {
  return {
    schema_version: 2,
    workflow_id: workflowId,
    name,
    description: "",
    nodes: [
      { id: "repository", kind: "input", label: "源码工作区", position: { x: 70, y: 100 }, config: { contract_id: "repo_path", label: "源码工作区", type: "directory", required: true, resolver: "workspace", role: "源码目录" } },
      { id: "target", kind: "input", label: "分析目标", position: { x: 70, y: 230 }, config: { contract_id: "analysis_target", label: "分析目标", type: "long_text", required: true, resolver: "manual", role: "用户逐字要求" } },
      { id: "analyze", kind: "agent", label: "源码分析", position: { x: 370, y: 150 }, config: { step_id: "analyze", goal: "读取源码并写出报告", provider: "builtin-llm", mcp_profiles: [], skill_ids: ["source-evidence-first"], required_artifacts: ["report.md"], input_ports: [{ id: "repo_path", type: "directory", required: true }, { id: "analysis_target", type: "long_text", required: true }], output_ports: [{ id: "report", type: "markdown" }], timeout_sec: 180, idle_timeout_sec: 60, failure_policy: "stop" } },
      { id: "report", kind: "output", label: "分析报告", position: { x: 690, y: 150 }, config: { output_id: "report", label: "分析报告", type: "markdown", artifact: "report.md", required: true, source_node_id: "analyze", source_port_id: "report" } },
    ],
    edges: [
      { id: "repository-analyze", kind: "data", source: { node_id: "repository", port_id: "value" }, target: { node_id: "analyze", port_id: "repo_path" } },
      { id: "target-analyze", kind: "data", source: { node_id: "target", port_id: "value" }, target: { node_id: "analyze", port_id: "analysis_target" } },
      { id: "analyze-report", kind: "data", source: { node_id: "analyze", port_id: "report" }, target: { node_id: "report", port_id: "value" } },
    ],
    settings: { stop_on_error: true, max_parallelism: 1 },
  };
}
