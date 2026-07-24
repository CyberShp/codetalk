import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

assertCanMutatePublicRuntime({ env: process.env, flowName: "Workbench V2 Task wizard real E2E" });

test("creates and restores a six-step Task with immutable execution and output overrides", async ({ page, request }) => {
  const stamp = Date.now();
  const workflowId = `task-wizard-e2e-${stamp}`;
  const workflowName = `Task Wizard E2E ${stamp}`;
  const taskName = `六步任务向导 ${stamp}`;
  const workspaceName = `Task Wizard Repo ${stamp}`;
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-task-wizard-")));
  fs.writeFileSync(path.join(repo, "README.md"), "# Real task wizard source\n", "utf8");
  execFileSync("git", ["init", "-q", repo]);

  const workspaceResponse = await request.post(`${backendBase}/api/workspaces`, { data: { name: workspaceName, repo_path: repo } });
  expect(workspaceResponse.ok()).toBeTruthy();
  const workflowResponse = await request.post(`${backendBase}/api/workbench/workflows`, {
    data: { id: workflowId, name: workflowName, description: "六步向导真实回归", authoring_graph: taskGraph(workflowId, workflowName) },
  });
  expect(workflowResponse.ok()).toBeTruthy();
  const draftId = (await workflowResponse.json()).current_draft_version_id as string;
  const publishResponse = await request.post(`${backendBase}/api/workbench/workflows/${workflowId}/versions/${draftId}/publish`, { data: {} });
  expect(publishResponse.ok()).toBeTruthy();
  const published = await publishResponse.json();

  await page.goto("/tasks/new", { waitUntil: "domcontentloaded" });
  await page.getByRole("radio", { name: new RegExp(workflowName) }).check();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByRole("textbox", { name: "任务名称 *" }).fill(taskName);
  await page.getByLabel("工作空间 *").selectOption({ label: workspaceName });
  await page.getByRole("textbox", { name: "描述" }).fill("验证用户输入逐字进入任务快照");
  await page.getByRole("button", { name: "保存并继续" }).click();

  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.locator(".ct-v2-notice[role=alert]")).toContainText("请填写必需输入：分析目标");
  await page.getByRole("textbox", { name: "分析目标 *" }).fill("逐文件分析 NVMe/TCP TLS 握手与失败恢复路径");
  await page.getByRole("button", { name: "保存并继续" }).click();

  await page.getByRole("checkbox", { name: "覆盖本任务" }).check();
  const provider = page.getByLabel("执行器");
  await provider.selectOption("agent-runtime:default-codex");
  await page.getByRole("checkbox", { name: "SFMEA sfmea" }).check();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page).toHaveURL(/step=5/);
  await page.reload();
  await expect(page).toHaveURL(/step=5/);
  await page.getByRole("button", { name: "上一步" }).click();
  await expect(page.getByLabel("执行器")).toHaveValue("agent-runtime:default-codex");
  await expect(page.getByRole("checkbox", { name: "SFMEA sfmea" })).toBeChecked();
  await page.getByRole("button", { name: "保存并继续" }).click();

  await page.getByRole("textbox", { name: "report 展示名称" }).fill("源码与 SFMEA 报告");
  await page.getByRole("textbox", { name: "report 文件名" }).fill("source-sfmea.md");
  await page.getByRole("button", { name: "添加输出" }).click();
  await page.getByRole("textbox", { name: "任务专用输出 1 名称" }).fill("结构化证据卡");
  await page.getByRole("textbox", { name: "任务专用输出 1 文件名" }).fill("evidence-cards.json");
  await page.getByLabel("任务专用输出 1 类型").selectOption("json");
  await page.getByLabel("任务专用输出 1 JSON 结构").selectOption("array");
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.getByText("输入", { exact: true }).locator(".."), "review shows frozen user input").toContainText("1/1");
  await expect(page.getByText("执行节点", { exact: true }).locator(".."), "review shows the frozen execution plan").toContainText("1");
  await expect(page.getByText("输出", { exact: true }).locator(".."), "review shows workflow and task-specific outputs").toContainText("2");
  await page.getByRole("button", { name: "保存为就绪任务" }).click();
  await expect(page.getByText("就绪", { exact: true })).toBeVisible();

  const taskId = page.url().split("/").pop() as string;
  const taskResponse = await request.get(`${backendBase}/api/workbench/tasks/${taskId}`);
  const compileResponse = await request.post(`${backendBase}/api/workbench/tasks/${taskId}/compile`);
  expect(taskResponse.ok()).toBeTruthy();
  expect(compileResponse.ok()).toBeTruthy();
  const task = await taskResponse.json();
  const effective = await compileResponse.json();
  expect(task.input_values.analysis_target).toBe("逐文件分析 NVMe/TCP TLS 握手与失败恢复路径");
  expect(effective.compiled_definition.steps[0].provider).toBe("agent-runtime:default-codex");
  expect(effective.compiled_definition.steps[0].skills).toContain("sfmea");
  expect(effective.compiled_definition.outputs.map((item: { artifact: string }) => item.artifact)).toEqual(["source-sfmea.md", "evidence-cards.json"]);
  expect(effective.compiled_definition.outputs[1].schema).toEqual({ type: "array" });
  expect(published.compiled_definition.steps[0].provider).toBe("builtin-llm");
  expect(published.compiled_definition.outputs[0].artifact).toBe("report.md");
});

function taskGraph(workflowId: string, name: string) {
  return {
    schema_version: 2, workflow_id: workflowId, name, description: "",
    nodes: [
      { id: "repository", kind: "input", label: "源码工作区", position: { x: 80, y: 90 }, config: { contract_id: "repo_path", label: "源码工作区", type: "directory", required: true, resolver: "workspace", role: "源码目录" } },
      { id: "target", kind: "input", label: "分析目标", position: { x: 80, y: 230 }, config: { contract_id: "analysis_target", label: "分析目标", type: "long_text", required: true, resolver: "manual", role: "用户逐字输入的分析要求" } },
      { id: "analyze", kind: "agent", label: "源码分析", position: { x: 390, y: 140 }, config: { step_id: "analyze", goal: "严格按用户目标阅读源码并写入产物", provider: "builtin-llm", mcp_profiles: [], skill_ids: ["artifact-contract", "source-evidence-first"], required_artifacts: ["report.md"], input_ports: [{ id: "repo_path", type: "directory", required: true }, { id: "analysis_target", type: "long_text", required: true }], output_ports: [{ id: "report", type: "markdown" }], timeout_sec: 900, idle_timeout_sec: 120, failure_policy: "stop" } },
      { id: "report", kind: "output", label: "分析报告", position: { x: 730, y: 140 }, config: { output_id: "report", label: "分析报告", type: "markdown", artifact: "report.md", required: true, source_node_id: "analyze", source_port_id: "report" } },
    ],
    edges: [
      { id: "repository-analyze", kind: "data", source: { node_id: "repository", port_id: "value" }, target: { node_id: "analyze", port_id: "repo_path" } },
      { id: "target-analyze", kind: "data", source: { node_id: "target", port_id: "value" }, target: { node_id: "analyze", port_id: "analysis_target" } },
      { id: "analyze-report", kind: "data", source: { node_id: "analyze", port_id: "report" }, target: { node_id: "report", port_id: "value" } },
    ],
    settings: { stop_on_error: true, max_parallelism: 1 },
  };
}
