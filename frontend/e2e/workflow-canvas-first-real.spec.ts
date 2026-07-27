import { chmodSync, mkdirSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { join } from "node:path";

import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";

import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "Workflow V3 Canvas First browser E2E",
});

type Template = "blank" | "free_source_analysis";

async function createThroughCanvasEntry(page: Page, template: Template, name: string) {
  const requestedUrls: string[] = [];
  page.on("request", (request) => requestedUrls.push(request.url()));

  await page.goto("/workflows/new", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("workflow-canvas-create-dialog")).toBeVisible();
  await expect(page.getByTestId("workflow-wizard-ready")).toHaveCount(0);
  await expect(page.getByLabel("工作流 ID")).toHaveCount(0);
  await expect(page.getByLabel(/input ID|output ID|step ID|port ID|contract ID/i)).toHaveCount(0);

  await page.getByLabel("工作流名称").fill(name);
  await page.getByTestId(`workflow-template-${template}`).check();
  await page.getByRole("button", { name: "创建并打开画布" }).click();

  await expect(page).toHaveURL(/\/workflows\/[^/?#]+\/designer(?:\?.*)?$/, { timeout: 20_000 });
  await expect(page.getByRole("region", { name: "工作流画布" })).toBeVisible();
  await expect(page.getByTestId("workflow-wizard-ready")).toHaveCount(0);

  return { canvas: page.getByRole("region", { name: "工作流画布" }), requestedUrls };
}

async function drag(page: Page, source: Locator, target: Locator) {
  await expect(source).toBeVisible();
  await expect(target).toBeVisible();
  const sourceBox = await source.boundingBox();
  const targetBox = await target.boundingBox();
  expect(sourceBox).not.toBeNull();
  expect(targetBox).not.toBeNull();
  if (!sourceBox || !targetBox) return;

  await page.mouse.move(sourceBox.x + sourceBox.width / 2, sourceBox.y + sourceBox.height / 2);
  await page.mouse.down({ button: "left" });
  await page.mouse.move(targetBox.x + targetBox.width / 2, targetBox.y + targetBox.height / 2, { steps: 12 });
  await page.mouse.up({ button: "left" });
}

async function waitForSave(page: Page) {
  await expect(page.getByTestId("workflow-save-status")).toHaveText("已保存", { timeout: 20_000 });
}

async function captureEvidence(page: Page, name: string) {
  const evidenceDir = process.env.CODETALK_E2E_ARTIFACT_DIR;
  if (!evidenceDir) return;
  mkdirSync(evidenceDir, { recursive: true });
  await page.screenshot({ path: join(evidenceDir, name), fullPage: false });
}

async function canvasNodePositions(nodes: Locator) {
  return nodes.evaluateAll((elements) => elements.map((element) => {
    const transform = (element as HTMLElement).style.transform;
    const match = transform.match(/translate\(([-\d.]+)px,\s*([-\d.]+)px\)/);
    if (!match) throw new Error(`Canvas node has no position transform: ${transform}`);
    return { x: Number(match[1]), y: Number(match[2]) };
  }));
}

async function createRealWorkspace(page: Page, label: string) {
  const root = process.env.CODETALK_TEMP_DIR ?? "/Volumes/Media/codetalk-runtime-tmp";
  const repoPath = join(root, `phase3-canvas-${Date.now()}-${Math.random().toString(16).slice(2)}`);
  mkdirSync(repoPath, { recursive: true });
  writeFileSync(join(repoPath, "README.md"), `# ${label}\n\nReal browser E2E source workspace.\n`, "utf8");
  writeFileSync(join(repoPath, "module.c"), "int phase3_canvas_e2e(void) { return 3; }\n", "utf8");

  await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
  await page.getByLabel("工作空间名称").fill(label);
  await page.getByLabel("代码仓库路径").fill(repoPath);
  await page.getByRole("button", { name: "创建工作空间" }).click();
  await expect(page).toHaveURL(/\/workspaces\/[^/?#]+$/, { timeout: 20_000 });
  return { label, repoPath };
}

async function enableDesignerFault(
  request: APIRequestContext,
  resource: "capabilities" | "providers" | "registry",
) {
  // This is a backend E2E fixture, deliberately not a browser route mock.
  // The main workflow still uses the visible browser creation flow above.
  const response = await request.put(`${backendBase}/api/workbench/test-support/workflow-designer-faults`, {
    data: { resource, status: 503 },
  });
  expect(response.status(), `test fixture must enable ${resource} failure`).toBe(204);
}

async function clearDesignerFault(request: APIRequestContext) {
  const response = await request.delete(`${backendBase}/api/workbench/test-support/workflow-designer-faults`);
  expect(response.status(), "test fixture must clear designer resource faults").toBe(204);
}

test("Canvas First: blank workflow is created entirely in the browser with generated hidden IDs and real canvas editing", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  const { canvas, requestedUrls } = await createThroughCanvasEntry(page, "blank", `Canvas blank ${Date.now()}`);

  await expect(canvas.getByText("节点库", { exact: true })).toBeVisible();
  await expect(canvas.getByTestId("workflow-palette-input")).toBeVisible();
  await expect(canvas.getByTestId("workflow-palette-agent")).toBeVisible();
  await expect(canvas.getByTestId("workflow-palette-output")).toBeVisible();
  await expect(canvas.getByTestId("workflow-palette-validator")).toHaveCount(0);
  await expect(canvas.getByTestId("workflow-palette-human-approval")).toHaveCount(0);

  const flow = canvas.locator(".react-flow");
  await expect(flow).toBeVisible();
  const flowBox = await flow.boundingBox();
  expect(flowBox).not.toBeNull();
  if (!flowBox) return;
  const beforePan = await canvas.locator(".react-flow__viewport").getAttribute("style");
  await page.mouse.move(flowBox.x + flowBox.width - 24, flowBox.y + flowBox.height - 24);
  await page.mouse.down({ button: "left" });
  await page.mouse.move(flowBox.x + flowBox.width - 144, flowBox.y + flowBox.height - 88, { steps: 8 });
  await page.mouse.up({ button: "left" });
  await expect.poll(() => canvas.locator(".react-flow__viewport").getAttribute("style")).not.toBe(beforePan);

  await canvas.getByTestId("workflow-palette-input").dblclick();
  await canvas.getByTestId("workflow-palette-agent").dblclick();
  await canvas.getByTestId("workflow-palette-output").dblclick();
  const nodes = canvas.locator(".react-flow__node-workflowNode");
  await expect(nodes).toHaveCount(3);

  await canvas.getByTestId("workflow-palette-input").dblclick();
  await expect(nodes).toHaveCount(4);
  await nodes.last().click();
  await page.keyboard.press("Delete");
  await expect(nodes).toHaveCount(3);
  await canvas.getByTitle("撤销").click();
  await expect(nodes).toHaveCount(4);
  await canvas.getByTitle("重做").click();
  await expect(nodes).toHaveCount(3);

  const source = nodes.nth(0);
  const sourceHandle = source.locator(".ct-v2-node-drag");
  const nodeBox = await source.boundingBox();
  const handleBox = await sourceHandle.boundingBox();
  expect(nodeBox).not.toBeNull();
  expect(handleBox).not.toBeNull();
  if (!nodeBox || !handleBox) return;
  await page.mouse.move(handleBox.x + 28, handleBox.y + 18);
  await page.mouse.down({ button: "left" });
  await page.mouse.move(handleBox.x + 96, handleBox.y + 44, { steps: 10 });
  await page.mouse.up({ button: "left" });
  await expect.poll(async () => (await source.boundingBox())?.x ?? 0).toBeGreaterThan(nodeBox.x + 40);

  await source.click();
  const inspector = page.getByRole("complementary", { name: "节点属性" });
  await expect(inspector).toBeVisible();
  await expect(inspector.getByLabel(/节点 ID|输入 ID|输出 ID|步骤 ID|端口 ID|契约 ID/)).toHaveCount(0);
  await inspector.getByRole("button", { name: "高级诊断" }).click();
  const diagnostics = inspector.getByTestId("workflow-technical-identifiers");
  await expect(diagnostics).toBeVisible();
  await expect(diagnostics.getByRole("textbox")).toHaveCount(0);
  await expect(diagnostics.locator("input[readonly], textarea[readonly]")).not.toHaveCount(0);

  const inputNode = canvas.getByRole("article", { name: /输入节点/ });
  const agentNode = canvas.getByRole("article", { name: /Agent节点/ });
  const outputNode = canvas.getByRole("article", { name: /输出节点/ });
  await drag(page, inputNode.getByLabel(/输出端口 value，类型 directory/), agentNode.getByLabel(/输入端口 repo_path，类型 directory/));
  await drag(page, agentNode.getByLabel(/输出端口 .*，类型 markdown/), outputNode.getByLabel(/输入端口 value，类型 markdown/));
  await expect(canvas.locator(".react-flow__edge")).toHaveCount(2);

  const edgeInteraction = canvas.locator(".react-flow__edge").last().locator(".react-flow__edge-interaction");
  const edgeBox = await edgeInteraction.boundingBox();
  expect(edgeBox).not.toBeNull();
  if (!edgeBox) return;
  await page.mouse.click(edgeBox.x + edgeBox.width / 2, edgeBox.y + edgeBox.height / 2);
  await page.keyboard.press("Delete");
  await expect(canvas.locator(".react-flow__edge")).toHaveCount(1);
  await canvas.getByTitle("撤销").click();
  await expect(canvas.locator(".react-flow__edge")).toHaveCount(2);
  await page.getByRole("button", { name: "保存" }).click();
  await waitForSave(page);

  await canvas.getByRole("article", { name: /源码工作区 输入节点/ }).click();
  await page.getByRole("button", { name: "高级诊断" }).click();
  const idsBeforeRefresh = await page.getByTestId("workflow-technical-identifiers").locator("input[readonly], textarea[readonly]").evaluateAll((elements) =>
    elements.map((element) => (element as HTMLInputElement | HTMLTextAreaElement).value),
  );
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByRole("region", { name: "工作流画布" })).toBeVisible();
  const desktopTitleBox = await page.locator(".ct-v2-designer-title h1").boundingBox();
  expect(desktopTitleBox).not.toBeNull();
  expect(desktopTitleBox?.x ?? 0).toBeGreaterThanOrEqual(224);
  expect(desktopTitleBox?.width ?? 0).toBeGreaterThan(80);
  await page.getByRole("article", { name: /输入节点/ }).click();
  await page.getByRole("button", { name: "高级诊断" }).click();
  const idsAfterRefresh = await page.getByTestId("workflow-technical-identifiers").locator("input[readonly], textarea[readonly]").evaluateAll((elements) =>
    elements.map((element) => (element as HTMLInputElement | HTMLTextAreaElement).value),
  );
  expect(idsAfterRefresh).toEqual(idsBeforeRefresh);
  expect(requestedUrls.some((url) => /(^|[^.])(?:xyflow|reactflow)[^/]*\.(?:js|css)(?:[?#]|$)/i.test(url) && !url.includes("localhost"))).toBeFalsy();
  await captureEvidence(page, "workflow-canvas-first-desktop.png");
});

test("Canvas First: rapid server-backed palette adds reserve distinct positions and survive refresh", async ({ page }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  const { canvas } = await createThroughCanvasEntry(page, "blank", `Canvas rapid add ${Date.now()}`);
  const paletteInput = canvas.getByTestId("workflow-palette-input");
  const paletteBox = await paletteInput.boundingBox();
  expect(paletteBox).not.toBeNull();
  if (!paletteBox) return;

  const x = paletteBox.x + paletteBox.width / 2;
  const y = paletteBox.y + paletteBox.height / 2;
  await page.mouse.dblclick(x, y, { delay: 8 });
  await page.mouse.dblclick(x, y, { delay: 8 });

  const nodes = canvas.locator(".react-flow__node-workflowNode");
  await expect(nodes).toHaveCount(2);
  const positionsBeforeRefresh = await canvasNodePositions(nodes);
  expect(new Set(positionsBeforeRefresh.map(({ x: nodeX, y: nodeY }) => `${nodeX}:${nodeY}`)).size).toBe(2);

  await waitForSave(page);
  await page.reload({ waitUntil: "domcontentloaded" });
  const refreshedNodes = page.getByRole("region", { name: "工作流画布" }).locator(".react-flow__node-workflowNode");
  await expect(refreshedNodes).toHaveCount(2);
  expect(await canvasNodePositions(refreshedNodes)).toEqual(positionsBeforeRefresh);
});

test("Canvas First: free-source workflow remains authorable on mobile and builds its trial form from named typed inputs", async ({ page, request }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 390, height: 844 });
  const workspace = await createRealWorkspace(page, `Canvas workspace ${Date.now()}`);
  const provider = await configureCanvasTrialProvider(request, workspace.repoPath);
  const { canvas } = await createThroughCanvasEntry(page, "free_source_analysis", `Canvas source ${Date.now()}`);

  await expect(canvas).toBeVisible();
  await expect(page.getByTestId("workflow-mobile-palette-toggle")).toBeVisible();
  await page.getByTestId("workflow-mobile-palette-toggle").click();
  await expect(canvas.getByTestId("workflow-palette-input")).toBeVisible();
  await canvas.getByTestId("workflow-palette-input").dblclick();

  const newInput = canvas.getByRole("article", { name: /输入材料 输入节点/ });
  await expect(newInput).toBeVisible();
  const inspector = page.getByRole("complementary", { name: "节点属性" });
  await expect(inspector.getByLabel("节点名称")).toHaveValue("输入材料");
  await inspector.getByLabel("节点名称").fill("开发设计文档");
  const editedInput = canvas.getByRole("article", { name: /开发设计文档 输入节点/ });
  await inspector.getByLabel("输入类型").selectOption("text");
  await expect(editedInput.getByLabel("输出端口 value，类型 text")).toBeVisible();
  await inspector.getByLabel("输入类型").selectOption("file");
  await expect(editedInput.getByLabel("输出端口 value，类型 file")).toBeVisible();
  await inspector.getByLabel("是否必填").check();
  await inspector.getByRole("button", { name: "关闭属性面板" }).click();

  const agent = canvas.getByRole("article", { name: /Agent节点/ });
  await agent.click();
  await inspector.getByLabel("执行器").selectOption(provider.id);
  await inspector.getByRole("button", { name: "增加输入端口" }).click();
  await inspector.getByLabel("输入端口 2 名称").fill("design_doc");
  await inspector.getByLabel("输入端口 2 类型").selectOption("file");
  await inspector.getByLabel("输入端口 2 是否必填").check();
  await inspector.getByRole("button", { name: "关闭属性面板" }).click();
  const agentBox = await agent.boundingBox();
  const agentHandleBox = await agent.locator(".ct-v2-node-drag").boundingBox();
  expect(agentBox).not.toBeNull();
  expect(agentHandleBox).not.toBeNull();
  if (!agentBox || !agentHandleBox) return;
  const dragStartX = agentHandleBox.x + agentHandleBox.width / 2;
  const dragStartY = agentHandleBox.y + agentHandleBox.height / 2;
  await page.mouse.move(dragStartX, dragStartY);
  await page.mouse.down({ button: "left" });
  await page.mouse.move(dragStartX + 48, dragStartY + 48, { steps: 8 });
  await page.mouse.up({ button: "left" });
  await expect.poll(async () => (await agent.boundingBox())?.y ?? 0).toBeGreaterThan(agentBox.y + 20);
  await inspector.getByRole("button", { name: "关闭属性面板" }).click();
  const designInput = canvas.getByRole("article", { name: /开发设计文档 输入节点/ });
  await designInput.getByLabel("输出端口 value，类型 file").dragTo(
    agent.getByLabel("输入端口 design_doc，类型 file"),
  );
  await expect(canvas.getByText("开发设计文档 · file → design_doc · file", { exact: true })).toBeVisible();
  await designInput.getByLabel("输出端口 value，类型 file").dragTo(
    agent.getByLabel("输入端口 repo_path，类型 directory"),
  );
  await expect(canvas.getByRole("alert")).toHaveText("该输入已绑定");

  await page.locator("header").getByRole("button", { name: "试运行" }).click();
  const trial = page.getByTestId("workflow-trial-form");
  await expect(trial).toBeVisible();
  await expect(trial.getByLabel("工作空间")).toHaveValue(/.+/);
  await expect(trial.getByLabel("工作空间").locator("option:checked")).toHaveText(workspace.label);
  await expect(trial.getByLabel("源码工作区 *")).toHaveCount(0);
  await expect(trial.getByLabel("开发设计文档 *")).toBeVisible();
  await expect(trial.getByLabel(/input ID|contract ID|port ID/i)).toHaveCount(0);
  await trial.getByLabel("开发设计文档 *").setInputFiles({
    name: "design.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# Design\n", "utf8"),
  });

  await trial.getByRole("button", { name: "启动试运行" }).click();
  await expect(trial).toContainText("运行已启动", { timeout: 30_000 });
  const runLink = trial.getByRole("link", { name: "查看运行" });
  await expect(runLink).toHaveAttribute("href", /\/workbench\?task_run_id=.+/);
  const runHref = await runLink.getAttribute("href");
  const runId = new URL(runHref ?? "", "http://localhost").searchParams.get("task_run_id") ?? "";
  expect(runId).toMatch(/^task_run_/);
  let receivedArtifact = "";
  await expect.poll(async () => {
    const response = await request.get(`${backendBase}/api/workbench/task-runs/${runId}/artifacts`);
    if (!response.ok()) return "";
    const manifest = await response.json() as { artifacts: Array<{ relative_path: string }> };
    receivedArtifact = manifest.artifacts.find((item) => item.relative_path.endsWith("/received_inputs.json"))?.relative_path ?? "";
    return receivedArtifact;
  }, { timeout: 30_000 }).toMatch(/received_inputs\.json$/);
  const receivedResponse = await request.get(
    `${backendBase}/api/workbench/task-runs/${runId}/artifacts/content/${receivedArtifact}`,
  );
  expect(receivedResponse.ok()).toBeTruthy();
  const receivedPayload = await receivedResponse.json() as { content: string };
  expect(receivedPayload.content).toContain("# Design");
  expect(receivedPayload.content).toContain("design_doc");
  const agentStatePath = receivedArtifact.replace(/received_inputs\.json$/, "agent_run.json");
  const agentStateResponse = await request.get(
    `${backendBase}/api/workbench/task-runs/${runId}/artifacts/content/${agentStatePath}`,
  );
  expect(agentStateResponse.ok()).toBeTruthy();
  const agentState = await agentStateResponse.json() as { content: string };
  expect(agentState.content).toContain(provider.id);

  await page.getByRole("button", { name: "保存" }).click();
  await waitForSave(page);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByRole("region", { name: "工作流画布" })).toBeVisible();
  const mobileTitleBox = await page.locator(".ct-v2-designer-title h1").boundingBox();
  expect(mobileTitleBox).not.toBeNull();
  expect(mobileTitleBox?.x ?? -1).toBeGreaterThanOrEqual(0);
  expect(mobileTitleBox?.width ?? 0).toBeGreaterThan(80);
  await expect(page.getByText("开发设计文档 · file → design_doc · file", { exact: true })).toBeVisible();
  await expect(page.locator("body")).not.toHaveCSS("overflow-x", "scroll");
  await captureEvidence(page, "workflow-canvas-first-mobile.png");
});

async function configureCanvasTrialProvider(
  request: APIRequestContext,
  repoPath: string,
) {
  const script = join(repoPath, "canvas_trial_provider.py");
  writeFileSync(script, [
    "import json, os, sys",
    "from pathlib import Path",
    "if '--version' in sys.argv: print('canvas-trial-provider 1.0'); raise SystemExit(0)",
    "payload = json.load(sys.stdin)",
    "resolved = payload.get('task_bundle', {}).get('resolved_inputs', {})",
    "texts = []",
    "for value in resolved.values():",
    "    if not isinstance(value, dict): continue",
    "    candidate = value.get('parsed_text_path') or value.get('copied_path') or value.get('path')",
    "    if candidate and Path(candidate).is_file(): texts.append(Path(candidate).read_text(encoding='utf-8'))",
    "artifact_dir = Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
    "artifact_dir.mkdir(parents=True, exist_ok=True)",
    "received = {'provider_contract': payload.get('runtime', {}).get('provider'), 'resolved_inputs': resolved, 'texts': texts, 'binding_marker': 'design_doc'}",
    "(artifact_dir / 'received_inputs.json').write_text(json.dumps(received, ensure_ascii=False), encoding='utf-8')",
    "(artifact_dir / 'report.md').write_text('# Canvas trial report\\n', encoding='utf-8')",
    "print('canvas trial provider completed')",
    "",
  ].join("\n"), "utf8");
  chmodSync(script, 0o755);
  const response = await request.post(`${backendBase}/api/settings/agent-runtimes`, {
    data: {
      name: `Canvas trial runtime ${Date.now()}`,
      provider: "custom",
      command: "python3.11",
      args: [script],
      prompt_transport: "stdin",
      output_mode: "plain",
      working_dir_mode: "project",
      timeout_seconds: 60,
      completion_mode: "process_exit",
      session_persistence: "none",
      requires_network: false,
      enabled: true,
    },
  });
  expect(response.status()).toBe(201);
  const runtime = await response.json() as { id: string };
  return { id: `agent-runtime:${runtime.id}` };
}

test("Canvas First: capability, provider, and registry failures are isolated and retriable", async ({ page, request }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await createThroughCanvasEntry(page, "free_source_analysis", `Canvas resources ${Date.now()}`);

  for (const resource of ["capabilities", "providers", "registry"] as const) {
    await enableDesignerFault(request, resource);
    try {
      await page.reload({ waitUntil: "domcontentloaded" });
      const resourceState = page.getByTestId(`workflow-resource-${resource}`);
      await expect(resourceState).toContainText("暂时不可用");
      await expect(resourceState).toContainText("Endpoint:");
      await expect(resourceState).toContainText("HTTP 503");
      await expect(resourceState).toContainText("Backend:");
      await expect(resourceState).toContainText("Frontend:");
      await expect(resourceState.getByRole("button", { name: "重试" })).toBeVisible();
      await expect(page.getByRole("region", { name: "工作流画布" })).toBeVisible();

      await clearDesignerFault(request);
      await resourceState.getByRole("button", { name: "重试" }).click();
      await expect(resourceState).toContainText("已就绪");
    } finally {
      await clearDesignerFault(request);
    }
  }
});

test("Canvas First: a published V1 workflow opens as read-only history", async ({ page, request }) => {
  test.setTimeout(60_000);
  const stamp = Date.now();
  const workflowId = `phase3-v1-read-only-${stamp}`;
  const name = `Phase 3 V1 read only ${stamp}`;
  const definition = {
    id: workflowId,
    name,
    version: 1,
    inputs: [{ id: "subject", type: "free_text", required: true }],
    steps: [{
      id: "analyze",
      type: "agent_task",
      provider: "builtin-llm",
      goal: "Summarize the supplied subject.",
      required_artifacts: ["report.md"],
    }],
    outputs: [{ id: "report", type: "markdown", from: "analyze", artifact: "report.md" }],
  };
  const dataDir = process.env.CODETALK_PLAYWRIGHT_DATA_DIR;
  expect(dataDir, "isolated Playwright data directory must be available").toBeTruthy();
  const seeded = spawnSync(
    process.env.CODETALK_BACKEND_PYTHON ?? "python3.11",
    [
      "-c",
      [
        "import json, sys",
        "from pathlib import Path",
        "from app.services.workflow_version_store import WorkflowVersionStore",
        "definition = json.loads(sys.argv[1])",
        "db_path = Path(sys.argv[2]) / 'workbench' / 'workflows.db'",
        "WorkflowVersionStore(db_path).ensure_legacy_published_workflows([definition])",
      ].join("; "),
      JSON.stringify(definition),
      dataDir ?? "",
    ],
    {
      cwd: join(process.cwd(), "../backend"),
      encoding: "utf8",
      env: process.env,
    },
  );
  expect(seeded.status, seeded.stderr || seeded.stdout).toBe(0);
  const headerResponse = await request.get(`${backendBase}/api/workbench/workflows/${workflowId}`);
  expect(headerResponse.ok(), await headerResponse.text()).toBeTruthy();
  const header = await headerResponse.json() as { v2: { published_version_id: string } };
  try {
    await page.goto(`/workflows/${workflowId}/designer`, { waitUntil: "domcontentloaded" });
    await expect(page).toHaveURL(
      new RegExp(`/workflows/${workflowId}/versions/${header.v2.published_version_id}$`),
      { timeout: 20_000 },
    );
    await expect(page.getByText("这是不可修改的发布快照", { exact: false })).toBeVisible();
    await expect(page.getByRole("button", { name: "创建新草稿" })).toHaveCount(0);
  } finally {
    await request.post(`${backendBase}/api/workbench/workflows/${workflowId}/archive`);
  }
});
