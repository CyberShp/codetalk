import { expect, test, type APIRequestContext } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "AI Thread V2 integration real E2E",
});

test("runs the AI thread to frozen Task, cockpit, artifact, and follow-up loop", async ({
  page,
  request,
}) => {
  test.setTimeout(180_000);
  const stamp = Date.now();
  const workflowId = `ai-thread-v2-e2e-${stamp}`;
  const workflowName = `AI Thread V2 联动 ${stamp}`;
  const workspaceName = `AI Thread V2 项目 ${stamp}`;
  const threadTitle = `NVMe TCP TLS 调查 ${stamp}`;
  const analysisTarget = "逐字分析 NVMe/TCP TLS 握手、失败恢复与可观测黑盒测试边界";
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-thread-v2-")));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.writeFileSync(path.join(repo, "README.md"), "# AI Thread V2 real integration\n", "utf8");
  fs.writeFileSync(
    path.join(repo, "lib", "nvmf", "tcp_tls.c"),
    "int nvmf_tcp_tls_handshake(void) { return 0; }\n",
    "utf8",
  );
  execFileSync("git", ["init", "-q", repo]);

  const runtime = await createArtifactReadingRuntime(request, stamp, repo);
  await configureIntegrationAgent(request, stamp);
  const workflowResponse = await request.post(`${backendBase}/api/workbench/workflows`, {
    data: {
      id: workflowId,
      name: workflowName,
      description: "真实验证 AI、任务、运行和产物双向联动",
      authoring_graph: integrationGraph(workflowId, workflowName),
    },
  });
  expect(workflowResponse.ok()).toBeTruthy();
  const workflowDraft = await workflowResponse.json();
  const publishResponse = await request.post(
    `${backendBase}/api/workbench/workflows/${workflowId}/versions/${workflowDraft.current_draft_version_id}/publish`,
    { data: {} },
  );
  const publishBody = await publishResponse.json();
  expect(publishResponse.ok(), JSON.stringify(publishBody, null, 2)).toBeTruthy();
  const published = publishBody;
  const publishedVersionId = String(published.version_id);

  await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
  await page.getByPlaceholder(/项目 A/).fill(workspaceName);
  await page.getByPlaceholder(/本地文件夹路径/).fill(repo);
  const createWorkspace = page.getByRole("button", { name: "创建工作空间" });
  await createWorkspace.hover();
  await createWorkspace.click();
  await page.waitForURL(/\/workspaces\/[0-9a-f-]{36}$/, { timeout: 30_000 });
  const workspaceId = page.url().split("/").pop() ?? "";
  expect(workspaceId).toBeTruthy();

  await page.goto("/ai", { waitUntil: "domcontentloaded" });
  const project = page.locator("button").filter({ hasText: workspaceName }).first();
  await expect(project).toBeVisible({ timeout: 20_000 });
  await project.hover();
  await project.click();
  await page.getByLabel("AI 线程执行器").selectOption(runtime.id);
  await page.getByLabel("线程工作流模板").selectOption(workflowId);
  await page.getByPlaceholder(/线程名称/).fill(threadTitle);
  const createThread = page.getByRole("button", { name: "新建线程" });
  await createThread.hover();
  await createThread.click();
  await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 20_000 });
  const sourceConversationId = page.url().split("/").pop() ?? "";

  await expect(page.getByText(new RegExp(`已绑定.*${escapeRegExp(workflowName)}`))).toBeVisible();
  await expect(page.getByText("固定发布版本", { exact: false })).toBeVisible();
  const createDraft = page.getByRole("button", { name: "创建任务草稿并补齐配置" });
  await createDraft.hover();
  await createDraft.click();
  await page.waitForURL(/\/tasks\/new\?task=task_[^&]+&step=3$/, { timeout: 20_000 });
  expect(page.url()).not.toContain("/workbench");
  const taskId = new URL(page.url()).searchParams.get("task") ?? "";
  expect(taskId).toMatch(/^task_/);

  await expect(page.getByRole("status")).toContainText("此任务来自 AI 线程");
  await page.getByRole("textbox", { name: "分析目标 *" }).fill(analysisTarget);
  await page.getByRole("button", { name: "保存并继续" }).click();

  await expect(page.getByRole("heading", { name: "确认执行配置" })).toBeVisible();
  const executionCards = page.locator(".ct-v2-execution-list article");
  await expect(executionCards).toHaveCount(2);
  await expect(executionCards.first()).toContainText("integration-agent");
  await expect(executionCards.first()).toContainText("source-evidence-first");
  await expect(executionCards.first()).toContainText("gitnexus");
  await executionCards.nth(1).getByRole("checkbox", { name: "覆盖本任务" }).check();
  await executionCards.nth(1).getByLabel("执行器").selectOption("integration-agent");
  await executionCards.nth(1).getByRole("checkbox", { name: /gitnexus/ }).check();
  await page.getByRole("button", { name: "保存并继续" }).click();

  await expect(page.getByRole("heading", { name: "确认交付输出" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "result 展示名称" })).toHaveValue("测试分析交付件");
  await expect(page.getByRole("textbox", { name: "result 文件名" })).toHaveValue("result.json");
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.getByText("输入", { exact: true }).locator("..")).toContainText("1/1");
  await expect(page.getByText("执行节点", { exact: true }).locator("..")).toContainText("2");
  await expect(page.getByText("输出", { exact: true }).locator("..")).toContainText("1");

  const saveAndRun = page.getByRole("button", { name: "保存并运行" });
  await saveAndRun.hover();
  await saveAndRun.click();
  await page.waitForURL(new RegExp(`/tasks/${taskId}/runs/task_run_`), { timeout: 30_000 });
  const runUrl = page.url();
  const runId = runUrl.split("/").pop() ?? "";
  expect(runId).toMatch(/^task_run_/);
  expect(runUrl).not.toContain("/workbench");

  await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("button", { name: "查看节点 collect" })).toBeVisible();
  await expect(page.getByRole("button", { name: "查看节点 deliver" })).toBeVisible();
  await expect(page.getByText("已完成", { exact: true }).first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("1 个可交付文件")).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".ct-v2-run-deliverables").getByRole("button", { name: /result\.json/ })).toBeVisible();
  await page.getByRole("button", { name: "查看节点 deliver" }).click();
  await expect(page.getByRole("heading", { name: "节点详情" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "MCP" }).locator("..")).toContainText("gitnexus");

  const taskResponse = await request.get(`${backendBase}/api/workbench/tasks/${taskId}`);
  const runResponse = await request.get(`${backendBase}/api/workbench/task-runs/${runId}`);
  expect(taskResponse.ok()).toBeTruthy();
  expect(runResponse.ok()).toBeTruthy();
  const task = await taskResponse.json();
  const run = await runResponse.json();
  expect(task.workflow_version_id).toBe(publishedVersionId);
  expect(task.workspace_id).toBe(workspaceId);
  expect(task.input_values.analysis_target).toBe(analysisTarget);
  expect(task.ai_origins).toEqual(
    expect.arrayContaining([expect.objectContaining({ conversation_id: sourceConversationId })]),
  );
  expect(run.task_id).toBe(taskId);
  expect(run.task_bundle.workflow_version_id).toBe(publishedVersionId);
  expect(JSON.stringify(run.task_bundle)).toContain("source-evidence-first");
  expect(JSON.stringify(run.task_bundle)).toContain("gitnexus");

  const evidenceDir = path.join(process.cwd(), "output", "playwright", "ai-thread-v2");
  fs.mkdirSync(evidenceDir, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.screenshot({ path: path.join(evidenceDir, "run-cockpit-desktop.png"), fullPage: false });
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);

  const discuss = page.getByRole("button", { name: "围绕本次运行继续分析" });
  await discuss.hover();
  await discuss.click();
  await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 20_000 });
  const followupConversationId = page.url().split("/").pop() ?? "";
  expect(followupConversationId).not.toBe(sourceConversationId);
  await expect(page.getByRole("region", { name: "关联任务运行" })).toContainText("Attempt 1");
  await expect(page.getByRole("link", { name: "打开运行驾驶舱" })).toHaveAttribute("href", `/tasks/${taskId}/runs/${runId}`);
  await page.getByLabel("当前 AI 执行器").selectOption(runtime.id);
  const followup = "读取本次运行的公开产物，说明是否拿到了 result.json，并给出下一步复测建议。";
  await page.getByLabel("AI 线程消息").fill(followup);
  const send = page.getByRole("button", { name: "发送" });
  await send.hover();
  await send.click();
  await expect(page.getByText("PUBLIC_ARTIFACT_READ_OK", { exact: false })).toBeVisible({ timeout: 45_000 });
  const followupAnswer = page.locator(".ct-codex-message").filter({ hasText: "PUBLIC_ARTIFACT_READ_OK" });
  await expect(followupAnswer.getByText(/task_artifact_manifest\.json/).first()).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
  await expect(page.getByLabel("AI 线程消息")).toBeVisible();
  await page.screenshot({ path: path.join(evidenceDir, "linked-ai-thread-mobile.png"), fullPage: false });
  await page.getByRole("link", { name: "打开运行驾驶舱" }).click();
  await expect(page).toHaveURL(runUrl);
});

async function createArtifactReadingRuntime(
  request: APIRequestContext,
  stamp: number,
  repo: string,
) {
  const script = path.join(repo, ".codetalk-e2e-artifact-reader.py");
  fs.writeFileSync(
    script,
    [
      "import sys",
      "prompt = sys.stdin.read()",
      "markers = ['task_artifact_manifest.json', 'result.json', 'integration-agent']",
      "ok = all(marker in prompt for marker in markers)",
      "state = 'PUBLIC_ARTIFACT_READ_OK' if ok else 'PUBLIC_ARTIFACT_NOT_FOUND'",
      "print('## 结论')",
      "print(state + '：已通过公开任务上下文核对本次运行，而非读取内部思考。')",
      "print('\\n## 运行证据')",
      "print('- task_artifact_manifest.json：公开产物清单。')",
      "print('- result.json：integration-agent 生成的最终测试分析交付件。')",
      "print('- 工作流固定版本和 Attempt 1 状态均已进入线程上下文。')",
      "print('\\n## 下一步建议')",
      "print('1. 下载 result.json 并核对输入目标与证据范围。')",
      "print('2. 复跑同一固定版本，比较交付件和节点耗时。')",
      "print('3. 对 TLS 握手失败、超时和恢复路径补充黑盒观测点。')",
      "",
    ].join("\n"),
    "utf8",
  );
  const response = await request.post(`${backendBase}/api/settings/agent-runtimes`, {
    data: {
      name: `AI V2 产物阅读执行器 ${stamp}`,
      command: "python3.11",
      args: [script],
      prompt_transport: "stdin",
      output_mode: "plain",
      working_dir_mode: "project",
      fixed_working_dir: "",
      env: {},
      health_command: "",
      timeout_seconds: 30,
      completion_mode: "process_exit",
      idle_complete_seconds: 5,
      sentinel_text: "",
      session_persistence: "none",
      resume_args: [],
      enabled: true,
    },
  });
  expect(response.status()).toBe(201);
  return (await response.json()) as { id: string; name: string };
}

async function configureIntegrationAgent(request: APIRequestContext, stamp: number) {
  const providerDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-workflow-agent-")));
  const providerScript = path.join(providerDir, "integration_agent.py");
  fs.writeFileSync(
    providerScript,
    [
      "#!/usr/bin/env python3",
      "import json, os, pathlib, sys, time",
      "prompt = sys.stdin.read()",
      "time.sleep(0.5)",
      "root = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
      "root.mkdir(parents=True, exist_ok=True)",
      "payload = {'status': 'ok', 'provider': 'integration-agent', 'prompt_chars': len(prompt), 'argv': sys.argv[1:]}",
      "(root / 'result.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')",
      "print(json.dumps({'status': 'ok', 'summary': 'integration-agent completed'}, ensure_ascii=False))",
      "",
    ].join("\n"),
    "utf8",
  );
  fs.chmodSync(providerScript, 0o755);
  const currentResponse = await request.get(`${backendBase}/api/settings/agent-providers`);
  expect(currentResponse.ok()).toBeTruthy();
  const current = await currentResponse.json();
  const providers = [
    ...((current.external_agent_custom_providers || []) as Array<Record<string, unknown>>)
      .filter((provider) => provider.id !== "integration-agent"),
    {
      id: "integration-agent",
      command: providerScript,
      prompt_transport: "stdin",
      supports_mcp: true,
      mcp_profiles: ["gitnexus"],
      supports_artifact_export: true,
      supports_json_output: true,
      label: `Integration Agent ${stamp}`,
    },
  ];
  const updateResponse = await request.put(`${backendBase}/api/settings/agent-providers`, {
    data: { ...current, external_agent_custom_providers: providers },
  });
  expect(updateResponse.ok()).toBeTruthy();
}

function integrationGraph(workflowId: string, name: string) {
  const agentConfig = (stepId: string, label: string, goal: string, outputPort: string) => ({
    step_id: stepId,
    label,
    goal,
    provider: "integration-agent",
    mcp_profiles: ["gitnexus"],
    skill_ids: ["source-evidence-first"],
    required_artifacts: stepId === "deliver" ? ["result.json"] : [],
    input_ports: [
      { id: "repo_path", type: "directory", required: true },
      { id: "analysis_target", type: "long_text", required: true },
    ],
    output_ports: [{ id: outputPort, type: "json" }],
    timeout_sec: 120,
    idle_timeout_sec: 30,
    failure_policy: "stop",
  });
  return {
    schema_version: 2,
    workflow_id: workflowId,
    name,
    description: "AI Thread V2 integration DAG",
    nodes: [
      { id: "repository", kind: "input", label: "源码工作区", position: { x: 60, y: 80 }, config: { contract_id: "repo_path", label: "源码工作区", type: "directory", required: true, resolver: "workspace", role: "源码目录" } },
      { id: "target", kind: "input", label: "分析目标", position: { x: 60, y: 220 }, config: { contract_id: "analysis_target", label: "分析目标", type: "long_text", required: true, resolver: "manual", role: "用户逐字输入的测试目标" } },
      { id: "collect", kind: "agent", label: "收集源码证据", position: { x: 350, y: 120 }, config: agentConfig("collect", "收集源码证据", "读取源码并形成证据快照", "evidence") },
      { id: "deliver", kind: "agent", label: "生成测试交付", position: { x: 620, y: 120 }, config: agentConfig("deliver", "生成测试交付", "基于证据生成测试分析交付件", "result") },
      { id: "result", kind: "output", label: "测试分析交付件", position: { x: 900, y: 120 }, config: { output_id: "result", label: "测试分析交付件", type: "json", artifact: "result.json", required: true, source_node_id: "deliver", source_port_id: "result", schema: { type: "object", required: ["status", "provider"], properties: { status: { type: "string" }, provider: { type: "string" } }, additionalProperties: true } } },
    ],
    edges: [
      { id: "repository-collect", kind: "data", source: { node_id: "repository", port_id: "value" }, target: { node_id: "collect", port_id: "repo_path" } },
      { id: "target-collect", kind: "data", source: { node_id: "target", port_id: "value" }, target: { node_id: "collect", port_id: "analysis_target" } },
      { id: "collect-deliver", kind: "dependency", source: { node_id: "collect", port_id: "done" }, target: { node_id: "deliver", port_id: "start" } },
      { id: "repository-deliver", kind: "data", source: { node_id: "repository", port_id: "value" }, target: { node_id: "deliver", port_id: "repo_path" } },
      { id: "target-deliver", kind: "data", source: { node_id: "target", port_id: "value" }, target: { node_id: "deliver", port_id: "analysis_target" } },
      { id: "deliver-result", kind: "data", source: { node_id: "deliver", port_id: "result" }, target: { node_id: "result", port_id: "value" } },
    ],
    settings: { stop_on_error: true, max_parallelism: 1 },
  };
}

async function horizontalOverflow(page: import("@playwright/test").Page) {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
