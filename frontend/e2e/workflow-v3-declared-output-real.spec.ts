import { expect, test, type APIRequestContext } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;
const runtimeTempRoot = process.env.CODETALK_TEMP_DIR || "/Volumes/Media/codetalk-runtime-tmp";

assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "Workflow V3 declared output real E2E",
});

test("runs a V3 report-only workflow through the task wizard without ghost deliverables", async ({ page, request }) => {
  test.setTimeout(120_000);
  const stamp = Date.now();
  const workflowName = `V3 Report Only ${stamp}`;
  const taskName = `V3 单报告任务 ${stamp}`;
  const workspaceName = `V3 单报告工作空间 ${stamp}`;
  const repo = createRepository(stamp);
  const designDocument = path.join(repo, "design-doc.md");
  const analysisTarget = `  保留开头空格的分析目标\n\n第二段前有一个空行。\n${"长文本输入必须逐字送达执行器。".repeat(120)}  `;
  const mrLink = `https://git.example.internal/storage/codetalk/merge_requests/${stamp}?view=diff`;
  const provider = await configureReportOnlyProvider(request, stamp, repo);
  let workflowId = "";
  let workflowCreated = false;
  try {
    const workspaceResponse = await request.post(`${backendBase}/api/workspaces`, {
      data: { name: workspaceName, repo_path: repo },
    });
    expect(workspaceResponse.ok()).toBeTruthy();

    const createdWorkflow = await createReportOnlyCanvas(request, workflowName, provider.id);
    workflowId = createdWorkflow.workflowId;
    workflowCreated = true;
    const publishResponse = await request.post(
      `${backendBase}/api/workbench/workflows/${workflowId}/versions/${createdWorkflow.versionId}/publish`,
      { data: { expected_revision: createdWorkflow.revision } },
    );
    expect(publishResponse.ok(), "V3 发布必须使用与运行相同的可执行契约").toBeTruthy();

    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/tasks/new", { waitUntil: "domcontentloaded" });
    await page.getByRole("radio", { name: new RegExp(workflowName) }).check();
    await page.getByRole("button", { name: "保存并继续" }).click();
    await page.getByLabel("任务名称 *").fill(taskName);
    await page.getByLabel("工作空间 *").selectOption({ label: workspaceName });
    await page.getByRole("button", { name: "保存并继续" }).click();
    await page.getByRole("textbox", { name: "分析目标 *" }).fill(analysisTarget);
    await page.getByLabel("开发设计文档 *").setInputFiles(designDocument);
    await page.getByRole("textbox", { name: "MR 链接 *" }).fill(mrLink);
    await expect(page.locator(".ct-v2-uploaded-files")).toHaveText("已选择 1 个文件", { timeout: 30_000 });
    await expect(page.getByRole("textbox", { name: "分析目标 *" })).toHaveValue(analysisTarget);
    await expect(page.getByRole("textbox", { name: "MR 链接 *" })).toHaveValue(mrLink);
    await page.getByRole("button", { name: "保存并继续" }).click();
    await page.getByRole("button", { name: "保存并继续" }).click();

    await expect(page.getByRole("heading", { name: "确认交付输出" })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "源码分析报告 展示名称" })).toHaveValue("源码分析报告");
    await expect(page.getByRole("textbox", { name: "源码分析报告 文件名" })).toHaveValue("report.md");
    await expect(page.getByRole("button", { name: "添加输出" })).toHaveCount(0);
    await expect(page.getByText("任务专用输出", { exact: false })).toHaveCount(0);
    await page.getByRole("button", { name: "保存并继续" }).click();
    await page.getByRole("button", { name: "保存并运行" }).click();

    await expect(page).toHaveURL(/\/tasks\/task_[^/]+\/runs\/task_run_/, { timeout: 30_000 });
    const runId = page.url().split("/").at(-1) ?? "";
    expect(runId).toMatch(/^task_run_/);

    const v3Status = page.getByLabel("V3 运行状态");
    await expect(v3Status).toBeVisible({ timeout: 60_000 });
    await expect(v3Status.getByText("执行", { exact: true })).toBeVisible();
    await expect(v3Status.getByText("已完成", { exact: true })).toBeVisible({ timeout: 60_000 });
    await expect(v3Status.getByText("产物校验", { exact: true })).toBeVisible();
    await expect(v3Status.getByText("已通过", { exact: true })).toBeVisible({ timeout: 60_000 });
    await expect(v3Status.getByText("专业治理", { exact: true })).toBeVisible();
    await expect(v3Status.getByText("未请求", { exact: true })).toBeVisible();
    const deliveryAxis = v3Status.locator("article").filter({ hasText: "交付" });
    await expect(deliveryAxis.getByText("交付", { exact: true })).toBeVisible();
    await expect(deliveryAxis.getByText("可交付", { exact: true })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText("1 个可交付文件", { exact: true })).toBeVisible({ timeout: 60_000 });
    await expect(page.locator(".ct-v2-run-deliverables").getByRole("button", { name: /report\.md/ })).toBeVisible();
    await expect(page.getByText("sfmea.json", { exact: true })).toHaveCount(0);
    await expect(page.getByText("black_box_cases.json", { exact: true })).toHaveCount(0);
    await expect(page.getByText("test_activity_contract", { exact: false })).toBeHidden();

    const artifactsResponse = await request.get(`${backendBase}/api/workbench/task-runs/${runId}/artifacts`);
    expect(artifactsResponse.ok()).toBeTruthy();
    const artifacts = await artifactsResponse.json() as { artifacts: Array<{ relative_path: string; audience: string }> };
    const deliverables = artifacts.artifacts.filter((item) => item.audience === "deliverable");
    expect(deliverables).toHaveLength(1);
    expect(path.basename(deliverables[0].relative_path)).toBe("report.md");
    expect(JSON.stringify(artifacts)).not.toContain("sfmea.json");
    expect(JSON.stringify(artifacts)).not.toContain("black_box_cases.json");
    expect(JSON.stringify(artifacts)).not.toContain("test_activity_contract");

    const receivedResponse = await request.get(
      `${backendBase}/api/workbench/task-runs/${runId}/artifacts/content/agent_runs/${createdWorkflow.agentNodeId}/received_inputs.json`,
    );
    expect(receivedResponse.ok(), "真实 CLI Provider 必须记录它从 stdin 收到的输入").toBeTruthy();
    const receivedPayload = await receivedResponse.json() as { content: string };
    const received = JSON.parse(receivedPayload.content) as {
      resolved_inputs: Record<string, unknown>;
      design_doc_text: string;
    };
    expect(received.resolved_inputs[createdWorkflow.inputIds.analysis_target]).toBe(analysisTarget);
    expect(received.resolved_inputs[createdWorkflow.inputIds.mr_link]).toBe(mrLink);
    expect(received.design_doc_text).toBe("# Design document\n\nThe report must preserve every supplied input.\n");

    const agentStateResponse = await request.get(
      `${backendBase}/api/workbench/task-runs/${runId}/artifacts/content/agent_runs/${createdWorkflow.agentNodeId}/agent_run.json`,
    );
    expect(agentStateResponse.ok()).toBeTruthy();
    const agentStatePayload = await agentStateResponse.json() as { content: string };
    expect((JSON.parse(agentStatePayload.content) as { requires_network: boolean }).requires_network).toBe(false);

    const sandboxPolicyResponse = await request.get(
      `${backendBase}/api/workbench/task-runs/${runId}/artifacts/content/agent_runs/${createdWorkflow.agentNodeId}/sandbox_policy.json`,
    );
    expect(sandboxPolicyResponse.ok()).toBeTruthy();
    const sandboxPolicyPayload = await sandboxPolicyResponse.json() as { content: string };
    const sandboxPolicy = JSON.parse(sandboxPolicyPayload.content) as {
      network_policy: { allowed: boolean; reason: string };
      read_paths: string[];
    };
    expect(sandboxPolicy.network_policy).toMatchObject({
      allowed: true,
      reason: "offline_agent_allowed",
    });
    expect(sandboxPolicy.read_paths.some((value) => value.endsWith("/parsed_text.txt"))).toBeTruthy();

    const evidenceDir = process.env.CODETALK_E2E_ARTIFACT_DIR || "/Volumes/Media/codetalk-e2e-artifacts";
    fs.mkdirSync(evidenceDir, { recursive: true });
    await page.screenshot({ path: path.join(evidenceDir, `workflow-v3-declared-output-${stamp}.png`), fullPage: false });
  } finally {
    if (workflowCreated) await request.post(`${backendBase}/api/workbench/workflows/${workflowId}/archive`);
    await provider.restore();
  }
});

function createRepository(stamp: number) {
  fs.mkdirSync(runtimeTempRoot, { recursive: true });
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(runtimeTempRoot, `codetalk-v3-report-${stamp}-`)));
  fs.mkdirSync(path.join(repo, "src"), { recursive: true });
  fs.writeFileSync(path.join(repo, "README.md"), "# V3 declared-output browser test\n", "utf8");
  fs.writeFileSync(path.join(repo, "src", "analysis.c"), "int report_only_analysis(void) { return 0; }\n", "utf8");
  fs.writeFileSync(path.join(repo, "design-doc.md"), "# Design document\n\nThe report must preserve every supplied input.\n", "utf8");
  execFileSync("git", ["init", "-q", repo]);
  return repo;
}

async function configureReportOnlyProvider(request: APIRequestContext, stamp: number, repo: string) {
  const script = path.join(repo, "report_only_provider.py");
  fs.writeFileSync(script, [
    "import json, os, sys",
    "from pathlib import Path",
    "if '--version' in sys.argv: print('report-only-provider 1.0'); raise SystemExit(0)",
    "stdin_text = sys.stdin.read()",
    "if 'CODETALK_PROBE_OK' in stdin_text:",
    "    print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'CODETALK_PROBE_OK'}}))",
    "    print(json.dumps({'type': 'turn.completed'}))",
    "    raise SystemExit(0)",
    "payload = json.loads(stdin_text)",
    "rendered = json.loads(payload.get('rendered_input') or '{}')",
    "resolved_inputs = rendered.get('resolved_inputs', {})",
    "design_doc = next((value for value in resolved_inputs.values() if isinstance(value, dict) and str(value.get('original_name') or value.get('path') or value.get('copied_path') or '').endswith('design-doc.md')), {})",
    "design_doc_path = Path(design_doc if isinstance(design_doc, str) else design_doc.get('parsed_text_path') or design_doc.get('copied_path') or design_doc.get('path', ''))",
    "if not design_doc_path.is_absolute(): design_doc_path = Path(payload.get('runtime', {}).get('cwd', '.')) / design_doc_path",
    "artifact_dir = Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
    "artifact_dir.mkdir(parents=True, exist_ok=True)",
    "design_doc_text = design_doc_path.read_text(encoding='utf-8') if design_doc_path.is_file() else f'__MISSING__:{design_doc_path}'",
    "received = {'resolved_inputs': resolved_inputs, 'design_doc_text': design_doc_text}",
    "(artifact_dir / 'received_inputs.json').write_text(json.dumps(received, ensure_ascii=False), encoding='utf-8')",
    "(artifact_dir / 'report.md').write_text('# Source analysis report\\n\\nOnly the declared report artifact was produced.\\n', encoding='utf-8')",
    "print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'report-only provider completed'}}))",
    "print(json.dumps({'type': 'turn.completed'}))",
    "",
  ].join("\n"), "utf8");
  fs.chmodSync(script, 0o755);

  const runtimeResponse = await request.post(`${backendBase}/api/settings/agent-runtimes`, {
    data: {
      name: `V3 report-only E2E runtime ${stamp}`,
      provider: "codex",
      command: "python3.11",
      args: [script],
      prompt_transport: "codex_exec_json",
      output_mode: "stream_json",
      working_dir_mode: "project",
      timeout_seconds: 60,
      completion_mode: "process_exit",
      session_persistence: "none",
      requires_network: false,
      enabled: true,
    },
  });
  expect(runtimeResponse.status()).toBe(201);
  const runtime = await runtimeResponse.json() as { id: string };
  return {
    id: `agent-runtime:${runtime.id}`,
    restore: async () => {
      const restoreResponse = await request.delete(
        `${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`,
      );
      expect(restoreResponse.status()).toBe(204);
    },
  };
}

async function createReportOnlyCanvas(
  request: APIRequestContext,
  name: string,
  providerRef: string,
) {
  type Port = { id: string };
  type Node = {
    id: string;
    ports: { inputs: Port[]; outputs: Port[] };
    config: { input_id?: string };
  };
  type Draft = { version_id: string; draft_revision: number };

  const createdResponse = await request.post(`${backendBase}/api/workbench/workflows/new`, {
    data: {
      template: "blank",
      name,
      description: "Only report.md is a declared deliverable.",
    },
  });
  expect(createdResponse.status(), await createdResponse.text()).toBe(201);
  const created = await createdResponse.json() as {
    workflow: { workflow_id: string };
    draft: Draft;
  };
  const workflowId = created.workflow.workflow_id;
  const versionId = created.draft.version_id;
  let revision = created.draft.draft_revision;

  const addNode = async (payload: Record<string, unknown>) => {
    const response = await request.post(
      `${backendBase}/api/workbench/workflows/${workflowId}/versions/${versionId}/nodes`,
      { data: { ...payload, expected_revision: revision } },
    );
    expect(response.status(), await response.text()).toBe(201);
    const result = await response.json() as { node: Node; draft: Draft };
    revision = result.draft.draft_revision;
    return result.node;
  };
  const addPort = async (nodeId: string, label: string, type: string) => {
    const response = await request.post(
      `${backendBase}/api/workbench/workflows/${workflowId}/versions/${versionId}/nodes/${nodeId}/ports`,
      { data: { direction: "inputs", label, type, required: true, expected_revision: revision } },
    );
    expect(response.status(), await response.text()).toBe(201);
    const result = await response.json() as { port: Port; draft: Draft };
    revision = result.draft.draft_revision;
    return result.port;
  };
  const addEdge = async (source: { node_id: string; port_id: string }, target: { node_id: string; port_id: string }) => {
    const response = await request.post(
      `${backendBase}/api/workbench/workflows/${workflowId}/versions/${versionId}/edges`,
      { data: { source, target, expected_revision: revision } },
    );
    expect(response.status(), await response.text()).toBe(201);
    const result = await response.json() as { draft: Draft };
    revision = result.draft.draft_revision;
  };

  const inputSpecs = [
    ["repo_path", "源码工作区", "directory", "workspace", 0],
    ["analysis_target", "分析目标", "long_text", "manual", 160],
    ["design_doc", "开发设计文档", "file", "local", 320],
    ["mr_link", "MR 链接", "mr_link", "manual", 480],
  ] as const;
  const inputNodes: Record<string, Node> = {};
  for (const [semanticId, label, type, resolver, y] of inputSpecs) {
    inputNodes[semanticId] = await addNode({
      kind: "input",
      label,
      position: { x: 0, y },
      config: { type, required: true, resolver },
    });
  }
  const agent = await addNode({
    kind: "agent",
    label: "源码分析",
    position: { x: 360, y: 80 },
    config: {
      provider_ref: providerRef,
      goal: "Read the workspace and create only the declared report artifact.",
      timeout_sec: 60,
      idle_timeout_sec: 20,
      retry_policy: { max_attempts: 1, backoff_seconds: 0 },
      failure_policy: "stop",
    },
  });
  const agentPorts: Record<string, Port> = { repo_path: agent.ports.inputs[0] };
  agentPorts.analysis_target = await addPort(agent.id, "analysis_target", "long_text");
  agentPorts.design_doc = await addPort(agent.id, "design_doc", "file");
  agentPorts.mr_link = await addPort(agent.id, "mr_link", "mr_link");
  const output = await addNode({
    kind: "output",
    label: "源码分析报告",
    position: { x: 720, y: 80 },
    config: { artifact: "report.md", media_type: "text/markdown", required: true },
  });

  for (const [semanticId] of inputSpecs) {
    const source = inputNodes[semanticId];
    await addEdge(
      { node_id: source.id, port_id: source.ports.outputs[0].id },
      { node_id: agent.id, port_id: agentPorts[semanticId].id },
    );
  }
  await addEdge(
    { node_id: agent.id, port_id: agent.ports.outputs[0].id },
    { node_id: output.id, port_id: output.ports.inputs[0].id },
  );

  return {
    workflowId,
    versionId,
    revision,
    agentNodeId: agent.id,
    inputIds: Object.fromEntries(
      inputSpecs.map(([semanticId]) => [semanticId, agentPorts[semanticId].id]),
    ) as Record<(typeof inputSpecs)[number][0], string>,
  };
}
