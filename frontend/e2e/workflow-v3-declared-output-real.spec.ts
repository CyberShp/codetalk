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
  const workflowId = `v3-report-only-${stamp}`;
  const workflowName = `V3 Report Only ${stamp}`;
  const taskName = `V3 单报告任务 ${stamp}`;
  const workspaceName = `V3 单报告工作空间 ${stamp}`;
  const repo = createRepository(stamp);
  const designDocument = path.join(repo, "design-doc.md");
  const analysisTarget = `  保留开头空格的分析目标\n\n第二段前有一个空行。\n${"长文本输入必须逐字送达执行器。".repeat(120)}  `;
  const mrLink = `https://git.example.internal/storage/codetalk/merge_requests/${stamp}?view=diff`;
  const provider = await configureReportOnlyProvider(request, stamp, repo);
  let workflowCreated = false;
  try {
    const workspaceResponse = await request.post(`${backendBase}/api/workspaces`, {
      data: { name: workspaceName, repo_path: repo },
    });
    expect(workspaceResponse.ok()).toBeTruthy();

    const workflowResponse = await request.post(`${backendBase}/api/workbench/workflows`, {
      data: {
        id: workflowId,
        name: workflowName,
        description: "V3 declared-output browser contract",
        authoring_graph: reportOnlyGraph(workflowId, workflowName, provider.id),
      },
    });
    expect(workflowResponse.status()).toBe(201);
    workflowCreated = true;
    const draft = await workflowResponse.json();
    const publishResponse = await request.post(
      `${backendBase}/api/workbench/workflows/${workflowId}/versions/${draft.current_draft_version_id}/publish`,
      { data: {} },
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
    await expect(page.getByRole("textbox", { name: "report 展示名称" })).toHaveValue("源码分析报告");
    await expect(page.getByRole("textbox", { name: "report 文件名" })).toHaveValue("report.md");
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
      `${backendBase}/api/workbench/task-runs/${runId}/artifacts/content/agent_runs/analyze/received_inputs.json`,
    );
    expect(receivedResponse.ok(), "真实 CLI Provider 必须记录它从 stdin 收到的输入").toBeTruthy();
    const receivedPayload = await receivedResponse.json() as { content: string };
    const received = JSON.parse(receivedPayload.content) as {
      resolved_inputs: Record<string, unknown>;
      design_doc_text: string;
    };
    expect(received.resolved_inputs.analysis_target).toBe(analysisTarget);
    expect(received.resolved_inputs.mr_link).toBe(mrLink);
    expect(received.design_doc_text).toBe("# Design document\n\nThe report must preserve every supplied input.\n");

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
    "payload = json.load(sys.stdin)",
    "resolved_inputs = payload.get('task_bundle', {}).get('resolved_inputs', {})",
    "design_doc = resolved_inputs.get('design_doc', {})",
    "design_doc_path = Path(design_doc if isinstance(design_doc, str) else design_doc.get('parsed_text_path') or design_doc.get('copied_path') or design_doc.get('path', ''))",
    "if not design_doc_path.is_absolute(): design_doc_path = Path(payload.get('runtime', {}).get('cwd', '.')) / design_doc_path",
    "artifact_dir = Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
    "artifact_dir.mkdir(parents=True, exist_ok=True)",
    "design_doc_text = design_doc_path.read_text(encoding='utf-8') if design_doc_path.is_file() else f'__MISSING__:{design_doc_path}'",
    "received = {'resolved_inputs': resolved_inputs, 'design_doc_text': design_doc_text}",
    "(artifact_dir / 'received_inputs.json').write_text(json.dumps(received, ensure_ascii=False), encoding='utf-8')",
    "(artifact_dir / 'report.md').write_text('# Source analysis report\\n\\nOnly the declared report artifact was produced.\\n', encoding='utf-8')",
    "print('report-only provider completed')",
    "",
  ].join("\n"), "utf8");
  fs.chmodSync(script, 0o755);

  const currentResponse = await request.get(`${backendBase}/api/settings/agent-providers`);
  expect(currentResponse.ok()).toBeTruthy();
  const current = await currentResponse.json() as { external_agent_custom_providers?: Array<Record<string, unknown>> };
  const id = `v3-report-only-provider-${stamp}`;
  const providers = [
    ...(current.external_agent_custom_providers || []).filter((item) => item.id !== id),
    {
      id,
      command: "python3.11",
      readonly_args: ["report_only_provider.py"],
      prompt_transport: "stdin",
      supports_mcp: false,
      mcp_profiles: [],
      supports_artifact_export: true,
      supports_json_output: false,
      label: "V3 report-only E2E provider",
    },
  ];
  const updateResponse = await request.put(`${backendBase}/api/settings/agent-providers`, {
    data: { ...current, external_agent_custom_providers: providers },
  });
  expect(updateResponse.ok()).toBeTruthy();
  return {
    id,
    restore: async () => {
      const restoreResponse = await request.put(`${backendBase}/api/settings/agent-providers`, { data: current });
      expect(restoreResponse.ok()).toBeTruthy();
    },
  };
}

function reportOnlyGraph(workflowId: string, name: string, providerRef: string) {
  return {
    schema_version: 3,
    workflow_id: workflowId,
    name,
    description: "Only report.md is a declared deliverable.",
    settings: { validation_profile: "artifact_only", stop_on_error: true, max_parallelism: 1 },
    nodes: [
      {
        id: "repo",
        kind: "input",
        label: "源码工作区",
        position: { x: 0, y: 0 },
        ports: { inputs: [], outputs: [{ id: "value", type: "directory" }] },
        config: { input_id: "repo_path", label: "源码工作区", type: "directory", required: true, resolver: "workspace" },
      },
      {
        id: "target",
        kind: "input",
        label: "分析目标",
        position: { x: 0, y: 160 },
        ports: { inputs: [], outputs: [{ id: "value", type: "long_text" }] },
        config: { input_id: "analysis_target", label: "分析目标", type: "long_text", required: true, resolver: "manual" },
      },
      {
        id: "design-doc",
        kind: "input",
        label: "开发设计文档",
        position: { x: 0, y: 320 },
        ports: { inputs: [], outputs: [{ id: "value", type: "file" }] },
        config: { input_id: "design_doc", label: "开发设计文档", type: "file", required: true, resolver: "local" },
      },
      {
        id: "mr-link",
        kind: "input",
        label: "MR 链接",
        position: { x: 0, y: 480 },
        ports: { inputs: [], outputs: [{ id: "value", type: "mr_link" }] },
        config: { input_id: "mr_link", label: "MR 链接", type: "mr_link", required: true, resolver: "manual" },
      },
      {
        id: "analyze",
        kind: "agent",
        label: "源码分析",
        position: { x: 360, y: 80 },
        ports: {
          inputs: [
            { id: "repo_path", type: "directory", required: true },
            { id: "analysis_target", type: "long_text", required: true },
            { id: "design_doc", type: "file", required: true },
            { id: "mr_link", type: "mr_link", required: true },
          ],
          outputs: [{ id: "report", type: "artifact", required: true }],
        },
        config: {
          handler_id: "agent",
          handler_version: 1,
          provider_ref: providerRef,
          goal: "Read the workspace and create only the declared report artifact.",
          prompt_template_version: 1,
          prompt_template: "{{node_goal}}\n{{bound_inputs}}\n{{output_contract}}",
          input_rendering: { preserve_user_text_verbatim: true, binding_order: ["repo_path", "analysis_target"] },
          timeout_sec: 60,
          idle_timeout_sec: 20,
          retry_policy: { max_attempts: 1, backoff_seconds: 0 },
          failure_policy: "stop",
          mcp_profiles: [],
          skill_ids: [],
          skill_instructions: [],
        },
      },
      {
        id: "report-output",
        kind: "output",
        label: "源码分析报告",
        position: { x: 720, y: 80 },
        ports: { inputs: [{ id: "value", type: "artifact", required: true }], outputs: [] },
        config: { output_id: "report", label: "源码分析报告", artifact: "report.md", media_type: "text/markdown", required: true, schema: null },
      },
    ],
    edges: [
      { id: "repo-analyze", kind: "data", source: { node_id: "repo", port_id: "value" }, target: { node_id: "analyze", port_id: "repo_path" } },
      { id: "target-analyze", kind: "data", source: { node_id: "target", port_id: "value" }, target: { node_id: "analyze", port_id: "analysis_target" } },
      { id: "design-analyze", kind: "data", source: { node_id: "design-doc", port_id: "value" }, target: { node_id: "analyze", port_id: "design_doc" } },
      { id: "mr-analyze", kind: "data", source: { node_id: "mr-link", port_id: "value" }, target: { node_id: "analyze", port_id: "mr_link" } },
      { id: "analyze-report", kind: "data", source: { node_id: "analyze", port_id: "report" }, target: { node_id: "report-output", port_id: "value" } },
    ],
  };
}
