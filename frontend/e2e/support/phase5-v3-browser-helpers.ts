import { expect, type APIRequestContext, type Locator, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

export const phase5FrontendPort = process.env.CODETALK_FRONTEND_PORT ?? "";
export const phase5BackendPort = process.env.CODETALK_BACKEND_PORT ?? "";
export const phase5BackendBase = `http://localhost:${phase5BackendPort}`;

export type ProviderMode = "report" | "invalid-schema" | "invalid-governance" | "professional-evidence";

export function assertPhase5IsolatedRuntime() {
  if (phase5FrontendPort !== "3233" || phase5BackendPort !== "3234") {
    throw new Error(
      "Phase 5 browser acceptance must run on isolated ports 3233/3234. " +
      "Set CODETALK_FRONTEND_PORT=3233 CODETALK_BACKEND_PORT=3234 CODETALK_REUSE_EXISTING_SERVER=0.",
    );
  }
}

export function createSourceRepository(label: string) {
  const root = process.env.CODETALK_TEMP_DIR ?? "/Volumes/Media/codetalk-runtime-tmp";
  fs.mkdirSync(root, { recursive: true });
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(root, "phase5-browser-")));
  fs.writeFileSync(path.join(repo, "README.md"), `# ${label}\n\nPhase 5 real-browser source fixture.\n`, "utf8");
  fs.writeFileSync(path.join(repo, "storage.c"), "int phase5_storage_path(void) { return 5; }\n", "utf8");
  execFileSync("git", ["init", "-q", repo]);
  return repo;
}

export async function createWorkspaceThroughUi(page: Page, name: string, repoPath: string) {
  await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
  await page.getByLabel("工作空间名称").fill(name);
  await page.getByLabel("代码仓库路径").fill(repoPath);
  await page.getByRole("button", { name: "创建工作空间" }).click();
  await expect(page).toHaveURL(/\/workspaces\/[^/?#]+$/, { timeout: 20_000 });
}

export async function configureFixtureProvider(
  request: APIRequestContext,
  repoPath: string,
  mode: ProviderMode,
) {
  const stamp = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const script = path.join(repoPath, `phase5-provider-${mode}-${stamp}.py`);
  const writes = mode === "report"
    ? ["(artifact_dir / 'report.md').write_text('# Generic source report\\n', encoding='utf-8')"]
    : mode === "invalid-schema"
      ? ["(artifact_dir / 'report.json').write_text('{\"not\": \"an array\"}', encoding='utf-8')"]
      : mode === "professional-evidence"
        ? [
            `source = Path(${JSON.stringify(path.join(repoPath, "storage.c"))})`,
            "source_text = source.read_text(encoding='utf-8')",
            "import hashlib",
            "evidence = [{'file_path': 'storage.c', 'start_line': 1, 'end_line': 1, 'excerpt': source_text.rstrip(), 'symbols': ['phase5_storage_path'], 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}]",
            "(artifact_dir / 'report.md').write_text('# Evidence collection report\\n', encoding='utf-8')",
            "(artifact_dir / 'verified-source-evidence.json').write_text(json.dumps(evidence), encoding='utf-8')",
          ]
      : [
          "(artifact_dir / 'report.md').write_text('# Storage test report\\n', encoding='utf-8')",
          "(artifact_dir / 'verified-source-evidence.json').write_text('[]', encoding='utf-8')",
        ];
  fs.writeFileSync(script, [
    "import json, os, sys",
    "from pathlib import Path",
    "if '--version' in sys.argv: print('phase5-browser-provider 1.0'); raise SystemExit(0)",
    "stdin_text = sys.stdin.read()",
    "if 'CODETALK_PROBE_OK' in stdin_text:",
    "    print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'CODETALK_PROBE_OK'}}))",
    "    print(json.dumps({'type': 'turn.completed'}))",
    "    raise SystemExit(0)",
    "artifact_dir = Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
    "artifact_dir.mkdir(parents=True, exist_ok=True)",
    ...writes,
    "print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'phase5 fixture completed'}}))",
    "print(json.dumps({'type': 'turn.completed'}))",
    "",
  ].join("\n"), "utf8");
  fs.chmodSync(script, 0o755);

  const response = await request.post(`${phase5BackendBase}/api/settings/agent-runtimes`, {
    data: {
      name: `Phase 5 ${mode} runtime ${stamp}`,
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
  expect(response.status(), await response.text()).toBe(201);
  const runtime = await response.json() as { id: string };
  return {
    id: `agent-runtime:${runtime.id}`,
    remove: async () => {
      const removed = await request.delete(
        `${phase5BackendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`,
      );
      expect(removed.status()).toBe(204);
    },
  };
}

export async function createReportWorkflowThroughUi(page: Page, name: string, providerRef: string) {
  await page.goto("/workflows/new", { waitUntil: "domcontentloaded" });
  await page.getByLabel("工作流名称").fill(name);
  await page.getByTestId("workflow-template-free_source_analysis").check();
  await page.getByRole("button", { name: "创建并打开画布" }).click();
  await expect(page).toHaveURL(/\/workflows\/[^/?#]+\/designer$/, { timeout: 20_000 });
  const workflowId = page.url().match(/\/workflows\/([^/?#]+)\/designer$/)?.[1] ?? "";
  expect(workflowId).toBeTruthy();
  await selectProviderThroughUi(page, providerRef);
  return workflowId;
}

export async function selectValidationProfileThroughUi(
  page: Page,
  profile: "schema" | "storage_test_design",
) {
  const profileSelect = page.getByLabel("验收模式");
  await expect(profileSelect).toBeVisible();
  await expect(
    profileSelect.locator('option[value="formal_release"]'),
    "Phase 6 formal release must not be selectable before human approval exists",
  ).toHaveCount(0);
  await profileSelect.selectOption(profile);
  await expect(profileSelect).toHaveValue(profile);
}

export async function configureSchemaReportThroughUi(page: Page) {
  const canvas = page.getByRole("region", { name: "工作流画布" });
  const report = canvas.getByRole("article", { name: /分析报告.*输出节点/ });
  await report.click();
  const inspector = page.getByRole("complementary", { name: "节点属性" });
  await inspector.getByLabel("节点名称").fill("结构化分析报告");
  await inspector.getByLabel("输出类型").selectOption("json");
  await inspector.getByLabel("结构规则").selectOption("array");
  await inspector.getByLabel("文件名").fill("report.json");
  await inspector.getByLabel("接收端口类型").selectOption("markdown");
  await inspector.getByRole("button", { name: "关闭属性面板" }).click();
}

export async function createProfessionalGovernanceWorkflowThroughUi(
  page: Page,
  options: { name: string; providerRef: string },
) {
  const workflowId = await createReportWorkflowThroughUi(page, options.name, options.providerRef);
  await selectValidationProfileThroughUi(page, "storage_test_design");
  const canvas = page.getByRole("region", { name: "工作流画布" });
  const inspector = page.getByRole("complementary", { name: "节点属性" });
  const palette = page.getByRole("complementary", { name: "节点库" });

  const agent = canvas.getByRole("article", { name: /源码分析.*Agent节点/ });
  await agent.click();
  await inspector.getByRole("button", { name: "增加输出端口" }).click();
  await inspector.getByLabel("输出端口 2 名称").fill("源码证据");
  await inspector.getByLabel("输出端口 2 名称").press("Enter");
  await inspector.getByLabel("输出端口 2 类型").selectOption("artifact");
  await inspector.getByRole("button", { name: "关闭属性面板" }).click();

  const governance = await addPaletteNode(page, palette, canvas, "governance");
  await inspector.getByLabel("节点名称").fill("存储测试专业设计");
  await inspector.getByLabel("Governance").selectOption("storage_test_design");
  await inspector.getByRole("button", { name: "关闭属性面板" }).click();

  const evidence = await addOutputThroughUi(page, palette, canvas, {
    label: "源码证据",
    artifact: "verified-source-evidence.json",
    roles: ["源码证据"],
  });
  const sfmea = await addOutputThroughUi(page, palette, canvas, {
    label: "SFMEA 风险清单",
    artifact: "risk-register.json",
    roles: ["SFMEA", "独立审查"],
  });
  const blackBox = await addOutputThroughUi(page, palette, canvas, {
    label: "黑盒测试用例",
    artifact: "test-matrix.json",
    roles: ["黑盒测试", "独立审查"],
  });

  await addPaletteNode(page, palette, canvas, "validator");
  await inspector.getByLabel("节点名称").fill("交付件存在校验");
  await inspector.getByLabel("Validator").selectOption("artifact_exists");
  const requiredOutputs = inspector.getByRole("group", { name: "验收交付件" });
  for (const label of ["分析报告", "源码证据", "SFMEA 风险清单", "黑盒测试用例"]) {
    await requiredOutputs.getByRole("checkbox", { name: new RegExp(label) }).check();
  }
  await inspector.getByRole("button", { name: "关闭属性面板" }).click();

  await canvas.getByTitle("适应画布").click();
  await dragConnection(
    page,
    agent.getByLabel("输出端口 源码证据，类型 artifact"),
    evidence.getByLabel("输入端口 value，类型 artifact"),
  );
  await dragConnection(
    page,
    agent.getByLabel("输出端口 源码证据，类型 artifact"),
    governance.getByLabel(/输入端口 源码证据，类型 artifact/i),
  );
  await dragConnection(
    page,
    governance.getByLabel(/输出端口 .*sfmea.*，类型 artifact/i),
    sfmea.getByLabel("输入端口 value，类型 artifact"),
  );
  await dragConnection(
    page,
    governance.getByLabel(/输出端口 黑盒测试用例，类型 artifact/i),
    blackBox.getByLabel("输入端口 value，类型 artifact"),
  );

  await page.locator("header").getByRole("button", { name: "保存" }).click();
  await expect(page.getByTestId("workflow-save-status")).toHaveText("已保存", { timeout: 20_000 });
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByLabel("验收模式")).toHaveValue("storage_test_design");
  await expect(page.getByText("源码证据 · artifact → 源码证据 · artifact", { exact: true })).toBeVisible();
  await expect(page.getByText("SFMEA 风险清单 · artifact → value · artifact", { exact: true })).toBeVisible();
  await expect(page.getByText("黑盒测试用例 · artifact → value · artifact", { exact: true })).toBeVisible();
  return { workflowId };
}

async function addPaletteNode(
  page: Page,
  palette: Locator,
  canvas: Locator,
  kind: "governance" | "validator" | "output",
) {
  const before = await canvas.locator(".react-flow__node-workflowNode").count();
  await palette.getByTestId(`workflow-palette-${kind}`).dblclick();
  await expect.poll(() => canvas.locator(".react-flow__node-workflowNode").count()).toBe(before + 1);
  const inspector = page.getByRole("complementary", { name: "节点属性" });
  await expect(inspector).toBeVisible();
  const nodeId = await page.getByTestId("workflow-selected-node-id").getAttribute("data-node-id");
  expect(nodeId).toBeTruthy();
  return canvas.getByTestId(`workflow-node-${nodeId}`);
}

async function addOutputThroughUi(
  page: Page,
  palette: Locator,
  canvas: Locator,
  options: { label: string; artifact: string; roles: string[] },
) {
  const output = await addPaletteNode(page, palette, canvas, "output");
  const inspector = page.getByRole("complementary", { name: "节点属性" });
  await inspector.getByLabel("接收端口类型").selectOption("artifact");
  await expect(inspector.getByLabel("接收端口类型")).toHaveValue("artifact");
  await inspector.getByLabel("节点名称").fill(options.label);
  await inspector.getByLabel("输出类型").selectOption("json");
  await expect(inspector.getByLabel("结构规则")).toBeVisible();
  await inspector.getByLabel("结构规则").selectOption("array");
  await inspector.getByLabel("文件名").fill(options.artifact);
  const roles = inspector.getByRole("group", { name: "验收角色" });
  for (const role of options.roles) await roles.getByRole("checkbox", { name: new RegExp(role) }).check();
  await inspector.getByRole("button", { name: "关闭属性面板" }).click();
  return output;
}

export async function dragConnection(page: Page, source: Locator, target: Locator) {
  await expect(source).toBeVisible();
  await expect(target).toBeVisible();
  const edgeCount = await page.locator(".react-flow__edge").count();
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const sourceBox = await source.boundingBox();
    const targetBox = await target.boundingBox();
    expect(sourceBox).not.toBeNull();
    expect(targetBox).not.toBeNull();
    if (!sourceBox || !targetBox) return;
    await page.mouse.move(sourceBox.x + sourceBox.width / 2, sourceBox.y + sourceBox.height / 2);
    await page.mouse.down({ button: "left" });
    await page.mouse.move(targetBox.x + targetBox.width / 2, targetBox.y + targetBox.height / 2, { steps: 18 });
    await page.mouse.up({ button: "left" });
    if (await page.locator(".react-flow__edge").count() === edgeCount + 1) return;
    await page.waitForTimeout(150);
  }
  await expect.poll(() => page.locator(".react-flow__edge").count()).toBe(edgeCount + 1);
}

export async function selectProviderThroughUi(page: Page, providerRef: string) {
  const canvas = page.getByRole("region", { name: "工作流画布" });
  await expect(canvas).toBeVisible();
  await canvas.getByRole("article", { name: /Agent节点/ }).click();
  const inspector = page.getByRole("complementary", { name: "节点属性" });
  const provider = inspector.getByLabel("执行器");
  await expect(provider.locator("option").filter({ hasText: providerRef })).toHaveCount(1, { timeout: 20_000 });
  await provider.selectOption(providerRef);
  await page.locator("header").getByRole("button", { name: "保存" }).click();
  await expect(page.getByTestId("workflow-save-status")).toHaveText("已保存", { timeout: 20_000 });
}

export async function publishCurrentWorkflowThroughUi(page: Page) {
  await page.locator("header").getByRole("button", { name: "发布" }).click();
  await expect(page).toHaveURL(/\/workflows\/[^/?#]+\/versions$/, { timeout: 30_000 });
}

export async function runPublishedWorkflowThroughUi(
  page: Page,
  options: {
    workflowName: string;
    taskName: string;
    workspaceName: string;
    inspectOutputs?: (page: Page) => Promise<void>;
  },
) {
  await page.goto("/tasks/new", { waitUntil: "domcontentloaded" });
  await page.getByRole("radio", { name: new RegExp(escapeRegExp(options.workflowName)) }).check();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByRole("textbox", { name: "任务名称 *" }).fill(options.taskName);
  await page.getByLabel("工作空间 *").selectOption({ label: options.workspaceName });
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.getByRole("heading", { name: "确认交付输出" })).toBeVisible();
  await options.inspectOutputs?.(page);
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByRole("button", { name: "保存并运行" }).click();
  await expect(page).toHaveURL(/\/tasks\/task_[^/]+\/runs\/task_run_/, { timeout: 30_000 });
  return page.url().split("/").at(-1) ?? "";
}

export function v3Axis(page: Page, label: "执行" | "产物校验" | "专业治理" | "交付") {
  return page.getByLabel("V3 运行状态").locator("article").filter({ hasText: label });
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
