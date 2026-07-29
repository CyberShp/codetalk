import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";

import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

const frontendPort = process.env.CODETALK_FRONTEND_PORT ?? "";
const backendPort = process.env.CODETALK_BACKEND_PORT ?? "";
const backendBase = `http://localhost:${backendPort}`;
const evidenceRoot = process.env.CODETALK_E2E_ARTIFACT_DIR
  ?? "/Volumes/Media/codetalk-e2e-artifacts/phase7/v3-interaction-concurrency";
const tempRoot = process.env.CODETALK_TEMP_DIR
  ?? "/Volumes/Media/codetalk-runtime-tmp/phase7-v3-interaction-concurrency";

assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "Phase 7 V3 interaction and concurrency browser acceptance",
});

if (
  frontendPort !== "3233" ||
  backendPort !== "3234" ||
  process.env.CODETALK_REUSE_EXISTING_SERVER !== "0" ||
  process.env.CODETALK_PLAYWRIGHT_GITNEXUS !== "0" ||
  process.env.GITNEXUS_BIN !== "/usr/bin/false" ||
  process.env.GITNEXUS_PORT !== "7101" ||
  process.env.GITNEXUS_BASE_URL !== "http://127.0.0.1:7101"
) {
  throw new Error("Phase 7 V3 acceptance requires isolated 3233/3234 servers and disabled GitNexus.");
}

type WorkflowFixture = { workflowId: string; versionId: string; analysisInputId: string };
type TaskRun = {
  task_run_id: string;
  task_id?: string;
  execution_status?: string;
  status?: string;
  workflow_snapshot?: { compiled_contract_version?: number };
  agent_runs?: Array<{ step_id: string }>;
};

test.beforeAll(() => {
  assertPort7100Unbound("before suite");
  fs.mkdirSync(evidenceRoot, { recursive: true });
  fs.mkdirSync(tempRoot, { recursive: true });
});

test.beforeEach(() => assertPort7100Unbound("before test"));
test.afterEach(() => assertPort7100Unbound("after test"));
test.afterAll(() => assertPort7100Unbound("after suite"));

test("Phase 7: server-owned V3 canvas supports desktop and mobile add, drag, connect, delete-edge, save, refresh, and real trial run without identifiers", async ({ page, request }, testInfo) => {
  test.setTimeout(180_000);
  const stamp = uniqueStamp(testInfo);
  const repo = createRepository(`interaction-${stamp}`);
  const workspaceName = `Phase 7 interaction workspace ${stamp}`;
  const runtime = await configureFixtureProvider(request, repo, stamp, "trial");

  try {
    const workspaceId = await createWorkspaceThroughUi(page, workspaceName, repo);
    await page.setViewportSize({ width: 1440, height: 900 });
    const workflowId = await createFreeSourceTemplateThroughUi(page, `Phase 7 desktop canvas ${stamp}`);
    const canvas = page.getByRole("region", { name: "工作流画布" });
    await assertNoVisibleInternalIdentifiers(page);
    await expectNoViewportOverflow(page);
    await selectFixtureProvider(page, runtime.id);

    await exerciseCanvasGraph(page, canvas);
    await saveAndRefresh(page);
    await expectNoViewportOverflow(page);
    await exerciseTrialRunThroughUi(page, workspaceName);
    await page.screenshot({ path: evidencePath(`desktop-v3-interaction-${stamp}.png`), fullPage: true });

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/workflows/${workflowId}/designer`, { waitUntil: "domcontentloaded" });
    await expect(canvas).toBeVisible();
    await expect(page.getByTestId("workflow-mobile-palette-toggle")).toBeVisible();
    await assertNoVisibleInternalIdentifiers(page);
    await exerciseCanvasGraph(page, canvas, { mobile: true });
    await saveAndRefresh(page);
    await expectNoViewportOverflow(page);
    await exerciseTrialRunThroughUi(page, workspaceName);
    await page.screenshot({ path: evidencePath(`mobile-v3-interaction-${stamp}.png`), fullPage: true });

    writeJson(`v3-interaction-${stamp}.json`, {
      workflow_id: workflowId,
      workspace_id: workspaceId,
      viewport_checks: ["1440x900", "390x844"],
      actions: ["add", "drag", "connect", "delete-edge", "save", "refresh", "trial-run"],
      gitnexus_7100_unbound: true,
    });
  } finally {
    await runtime.remove();
  }
});

test("Phase 7: an unknown frozen compiled-contract version has an actionable real-browser compatibility notice", async ({ page, request }, testInfo) => {
  test.setTimeout(90_000);
  const stamp = uniqueStamp(testInfo);
  const repo = createRepository(`unsupported-${stamp}`);
  const workspaceId = await createWorkspace(request, `Phase 7 unsupported workspace ${stamp}`, repo);
  const workflow = await createPublishedFreeSourceWorkflow(request, `Phase 7 unsupported workflow ${stamp}`);
  const taskId = await createReadyTask(request, workflow, workspaceId, `Phase 7 unsupported task ${stamp}`, "unsupported contract");
  const runId = await createRun(request, taskId);

  seedUnknownContractVersion(runId, 999);
  await page.goto(`/tasks/${taskId}/runs/${runId}`, { waitUntil: "domcontentloaded" });
  await expect(page.locator(".ct-v2-run-error[role='alert']")).toContainText("冻结契约版本不受支持：999");
  const compatibility = page.getByRole("region", { name: "运行版本兼容性" });
  await expect(compatibility).toContainText("不支持的冻结契约版本");
  await expect(compatibility).toContainText("历史运行仍可查看和下载");
  await expect(compatibility).toContainText("复制为受支持的 V3 工作流");
  await page.screenshot({ path: evidencePath(`unknown-contract-${stamp}.png`), fullPage: true });
  writeJson(`unknown-contract-${stamp}.json`, { task_id: taskId, task_run_id: runId, compiled_contract_version: 999 });
});

test("Phase 7: V3 task agents are scheduler-owned in the real product and direct execution fails closed", async ({ page, request }, testInfo) => {
  test.setTimeout(90_000);
  const stamp = uniqueStamp(testInfo);
  const repo = createRepository(`scheduler-authority-${stamp}`);
  const workspaceId = await createWorkspace(request, `Phase 7 scheduler authority ${stamp}`, repo);
  const workflow = await createPublishedFreeSourceWorkflow(request, `Phase 7 scheduler authority ${stamp}`);
  const taskId = await createReadyTask(request, workflow, workspaceId, `Phase 7 scheduler authority task ${stamp}`, "scheduler authority");
  const runId = await createRun(request, taskId);
  const run = await getRun(request, runId);
  const stepId = run.agent_runs?.[0]?.step_id ?? "";
  const beforeResponse = await request.get(`${backendBase}/api/workbench/task-runs/${runId}`);
  const beforeBody = await beforeResponse.text();
  const eventsBefore = await getEvents(request, runId);

  expect(run.workflow_snapshot?.compiled_contract_version).toBe(3);
  expect(stepId).toBeTruthy();

  const direct = await request.post(
    `${backendBase}/api/workbench/task-runs/${runId}/agent-runs/${encodeURIComponent(stepId)}/execute`,
    { data: { timeout_sec: 30 } },
  );
  const directBody = await direct.json() as { detail?: { code?: string; message?: string } };
  expect(direct.status(), JSON.stringify(directBody)).toBe(409);
  expect(directBody.detail?.code).toBe(
    "workflow_v3_scheduler_authority",
  );
  const afterResponse = await request.get(`${backendBase}/api/workbench/task-runs/${runId}`);
  const afterBody = await afterResponse.text();
  const eventsAfter = await getEvents(request, runId);
  expect(afterBody).toBe(beforeBody);
  expect(eventsAfter).toEqual(eventsBefore);

  await page.goto(`/tasks/${taskId}/runs/${runId}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByLabel("V3 运行状态")).toBeVisible();
  await expect(page.getByRole("button", { name: "Execute", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Validate", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Materialize", exact: true })).toHaveCount(0);
  await page.screenshot({ path: evidencePath(`v3-scheduler-authority-${stamp}.png`), fullPage: true });
  writeJson(`v3-scheduler-authority-${stamp}.json`, {
    task_id: taskId,
    task_run_id: runId,
    step_id: stepId,
    request: {
      method: "POST",
      path: `/api/workbench/task-runs/${runId}/agent-runs/${stepId}/execute`,
      body: { timeout_sec: 30 },
    },
    direct_response: {
      status: direct.status(),
      body: directBody,
    },
    attempt_before: JSON.parse(beforeBody),
    attempt_after: JSON.parse(afterBody),
    attempt_before_sha256: sha256(beforeBody),
    attempt_after_sha256: sha256(afterBody),
    events_before: eventsBefore,
    events_after: eventsAfter,
  });
});

test("Phase 7: three concurrent V3 tasks retain distinct task/run identities, events, and artifact manifests while their real product pages open", async ({ page, request }, testInfo) => {
  test.setTimeout(180_000);
  const stamp = uniqueStamp(testInfo);
  const repo = createRepository(`concurrent-${stamp}`);
  const runtime = await configureFixtureProvider(request, repo, stamp, "concurrent");
  const workspaceId = await createWorkspace(request, `Phase 7 concurrent workspace ${stamp}`, repo);
  const workflow = await createPublishedFreeSourceWorkflow(request, `Phase 7 concurrent workflow ${stamp}`, runtime.id);

  try {
    const taskIds = await Promise.all([0, 1, 2].map((index) => createReadyTask(
      request,
      workflow,
      workspaceId,
      `Phase 7 concurrent task ${stamp}-${index + 1}`,
      `concurrent marker ${index + 1}`,
    )));
    expect(new Set(taskIds).size).toBe(3);

    const runIds = await Promise.all(taskIds.map((taskId) => createRun(request, taskId)));
    expect(new Set(runIds).size).toBe(3);
    await Promise.all(runIds.map((runId) => executeRun(request, runId)));
    await Promise.all(runIds.map((runId) => expect.poll(() => runStatus(request, runId), { timeout: 90_000 }).toBe("completed")));

    const evidence = await Promise.all(runIds.map(async (runId, index) => {
      const [run, events, manifest] = await Promise.all([
        getRun(request, runId),
        getEvents(request, runId),
        getManifest(request, runId),
      ]);
      expect(run.task_id).toBe(taskIds[index]);
      expect(events.length).toBeGreaterThan(0);
      expect(manifest).toHaveLength(1);
      expect(path.posix.basename(manifest[0].relative_path)).toBe("report.md");
      expect(manifest[0].relative_path).toMatch(/^agent_runs\/[^/]+\/report\.md$/);
      return { task_id: taskIds[index], task_run_id: runId, event_ids: events.map((item) => item.event_id), manifest };
    }));
    for (const item of evidence) {
      expect(new Set(item.event_ids).size).toBe(item.event_ids.length);
    }
    const eventKeys = evidence.flatMap((item) => item.event_ids.map((eventId) => `${item.task_run_id}:${eventId}`));
    expect(new Set(eventKeys).size).toBe(eventKeys.length);

    for (let index = 0; index < taskIds.length; index += 1) {
      await page.goto(`/tasks/${taskIds[index]}`, { waitUntil: "domcontentloaded" });
      await expect(page.getByRole("heading", { name: new RegExp(`Phase 7 concurrent task ${escapeRegExp(stamp)}-${index + 1}`) })).toBeVisible();
      await page.goto(`/tasks/${taskIds[index]}/runs/${runIds[index]}`, { waitUntil: "domcontentloaded" });
      await expect(page.getByRole("heading", { name: new RegExp(`Phase 7 concurrent task ${escapeRegExp(stamp)}-${index + 1}`) })).toBeVisible();
      await expect(page.getByLabel("V3 运行状态")).toBeVisible();
    }
    await page.screenshot({ path: evidencePath(`concurrent-v3-runs-${stamp}.png`), fullPage: true });
    writeJson(`concurrent-v3-runs-${stamp}.json`, { task_ids: taskIds, task_run_ids: runIds, runs: evidence });
  } finally {
    await runtime.remove();
  }
});

async function createWorkspaceThroughUi(page: Page, name: string, repoPath: string) {
  await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
  await page.getByLabel("工作空间名称").fill(name);
  await page.getByLabel("代码仓库路径").fill(repoPath);
  await page.getByRole("button", { name: "创建工作空间" }).click();
  await expect(page).toHaveURL(/\/workspaces\/[^/?#]+$/, { timeout: 20_000 });
  const workspaceId = page.url().split("/").at(-1) ?? "";
  expect(workspaceId).toBeTruthy();
  return workspaceId;
}

async function createFreeSourceTemplateThroughUi(page: Page, name: string) {
  await page.goto("/workflows/new", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("workflow-template-free_source_analysis")).toBeChecked();
  await page.getByLabel("工作流名称").fill(name);
  await page.getByRole("button", { name: "创建并打开画布" }).click();
  await expect(page).toHaveURL(/\/workflows\/wf_[^/?#]+\/designer$/, { timeout: 20_000 });
  await expect(page.getByRole("region", { name: "工作流画布" })).toBeVisible();
  return page.url().match(/\/workflows\/([^/?#]+)\/designer/)?.[1] ?? "";
}

async function exerciseCanvasGraph(page: Page, canvas: Locator, options: { mobile?: boolean } = {}) {
  if (options.mobile) await page.getByTestId("workflow-mobile-palette-toggle").click();
  const palette = canvas.getByTestId("workflow-palette-output");
  await expect(palette).toBeVisible();
  const beforeCount = await canvas.locator(".react-flow__node-workflowNode").count();
  await palette.dblclick();
  await expect(canvas.locator(".react-flow__node-workflowNode")).toHaveCount(beforeCount + 1);
  const addedNode = canvas.locator(".react-flow__node-workflowNode").last();
  await expect(addedNode).toBeVisible();

  if (options.mobile) {
    await page.getByRole("button", { name: "关闭属性面板" }).click();
    await revealNodeInCompactCanvas(page, canvas, addedNode);
  }

  const dragHandle = addedNode.locator(".ct-v2-node-drag");
  const before = await addedNode.boundingBox();
  const handle = await dragHandle.boundingBox();
  expect(before).not.toBeNull();
  expect(handle).not.toBeNull();
  if (!before || !handle) return;
  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();
  if (!viewport) return;
  const startX = Math.min(viewport.width - 8, Math.max(8, handle.x + handle.width / 2));
  const startY = Math.min(viewport.height - 8, Math.max(8, handle.y + handle.height / 2));
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.mouse.move(startX + 24, startY + (options.mobile ? -160 : 48), { steps: 8 });
  await page.mouse.up();
  await expect.poll(async () => {
    const after = await addedNode.boundingBox();
    return after ? Math.hypot(after.x - before.x, after.y - before.y) : 0;
  }).toBeGreaterThan(20);

  if (options.mobile) {
    await page.getByRole("button", { name: "关闭属性面板" }).click();
    await expect(page.getByRole("complementary", { name: "节点属性" })).toHaveCount(0);
  }

  const agent = canvas.getByRole("article", { name: /Agent节点/ }).first();
  await dragConnection(
    page,
    agent.getByLabel(/输出端口 .*，类型 markdown/),
    addedNode.getByLabel(/输入端口 value，类型 markdown/),
    { clickToConnect: options.mobile },
  );
  const edges = canvas.locator(".react-flow__edge");
  const edge = edges.last();
  const edgeCount = await edges.count();
  const nodeCount = await canvas.locator(".react-flow__node-workflowNode").count();
  await edge.focus();
  await page.keyboard.press("Enter");
  await expect(edge).toHaveClass(/selected/);
  await page.keyboard.press("Delete");
  await expect(edges).toHaveCount(edgeCount - 1);
  await expect(canvas.locator(".react-flow__node-workflowNode")).toHaveCount(nodeCount);

  await addedNode.click();
  await page.keyboard.press("Delete");
  await expect(canvas.locator(".react-flow__node-workflowNode")).toHaveCount(beforeCount);
}

async function revealNodeInCompactCanvas(page: Page, canvas: Locator, node: Locator) {
  const nodeBox = await node.boundingBox();
  const paneBox = await canvas.locator(".react-flow__pane").boundingBox();
  const viewport = page.viewportSize();
  expect(nodeBox).not.toBeNull();
  expect(paneBox).not.toBeNull();
  expect(viewport).not.toBeNull();
  if (!nodeBox || !paneBox || !viewport) return;

  const leftCorrection = Math.max(0, 16 - nodeBox.x);
  const rightCorrection = Math.min(0, viewport.width - 16 - (nodeBox.x + nodeBox.width));
  const horizontalCorrection = leftCorrection || rightCorrection;
  if (Math.abs(horizontalCorrection) < 1) return;

  const startX = Math.min(paneBox.x + paneBox.width - Math.abs(horizontalCorrection) - 16, paneBox.x + paneBox.width * 0.55);
  const startY = paneBox.y + Math.min(72, paneBox.height * 0.25);
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.mouse.move(startX + horizontalCorrection, startY, { steps: 10 });
  await page.mouse.up();
  await expect.poll(async () => (await node.boundingBox())?.x ?? nodeBox.x).not.toBe(nodeBox.x);
}

async function dragConnection(page: Page, source: Locator, target: Locator, options: { clickToConnect?: boolean } = {}) {
  await expect(source).toBeVisible();
  await expect(target).toBeVisible();
  const before = await page.locator(".react-flow__edge").count();
  if (options.clickToConnect) {
    await source.click();
    await target.click();
    await expect.poll(() => page.locator(".react-flow__edge").count()).toBe(before + 1);
    return;
  }
  const sourceBox = await source.boundingBox();
  const targetBox = await target.boundingBox();
  expect(sourceBox).not.toBeNull();
  expect(targetBox).not.toBeNull();
  if (!sourceBox || !targetBox) return;
  await page.mouse.move(sourceBox.x + sourceBox.width / 2, sourceBox.y + sourceBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(targetBox.x + targetBox.width / 2, targetBox.y + targetBox.height / 2, { steps: 16 });
  await page.mouse.up();
  await expect.poll(() => page.locator(".react-flow__edge").count()).toBe(before + 1);
}

async function saveAndRefresh(page: Page) {
  await page.locator("header").getByRole("button", { name: "保存" }).click();
  await expect(page.getByTestId("workflow-save-status")).toHaveText("已保存", { timeout: 20_000 });
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByRole("region", { name: "工作流画布" })).toBeVisible();
}

async function exerciseTrialRunThroughUi(page: Page, workspaceName: string) {
  await page.getByRole("button", { name: "试运行" }).first().click();
  const trial = page.getByTestId("workflow-trial-form");
  await expect(trial).toBeVisible();
  await trial.getByLabel("工作空间").selectOption({ label: workspaceName });
  await trial.getByLabel("分析目标 *").fill("Phase 7 browser trial run");
  await trial.getByRole("button", { name: "启动试运行" }).click();
  const link = trial.getByRole("link", { name: "查看运行" });
  await expect(link).toBeVisible({ timeout: 60_000 });
  await link.click();
  await expect(page.getByRole("main")).toBeVisible();
}

async function selectFixtureProvider(page: Page, providerRef: string) {
  const canvas = page.getByRole("region", { name: "工作流画布" });
  await canvas.getByRole("article", { name: /Agent节点/ }).first().click();
  const inspector = page.getByRole("complementary", { name: "节点属性" });
  const provider = inspector.getByLabel("执行器");
  await expect(provider.locator("option").filter({ hasText: providerRef })).toHaveCount(1, { timeout: 20_000 });
  await provider.selectOption(providerRef);
  await saveAndRefresh(page);
}

async function assertNoVisibleInternalIdentifiers(page: Page) {
  await expect(page.getByLabel(/工作流 ID|节点 ID|输入 ID|输出 ID|步骤 ID|端口 ID|契约 ID/i)).toHaveCount(0);
}

async function expectNoViewportOverflow(page: Page) {
  const size = await page.evaluate(() => ({ client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
  expect(size.scroll, `horizontal overflow: ${JSON.stringify(size)}`).toBeLessThanOrEqual(size.client + 1);
}

function createRepository(label: string) {
  const repo = fs.mkdtempSync(path.join(tempRoot, "phase7-v3-"));
  fs.writeFileSync(path.join(repo, "README.md"), `# ${label}\n`, "utf8");
  fs.writeFileSync(path.join(repo, "source.c"), "int phase7_v3_fixture(void) { return 7; }\n", "utf8");
  execFileSync("git", ["init", "-q", repo]);
  return repo;
}

async function configureFixtureProvider(request: APIRequestContext, repo: string, stamp: string, label: string) {
  const script = path.join(repo, `phase7-${label}-${stamp}.py`);
  fs.writeFileSync(script, [
    "import json, os, sys, time",
    "from pathlib import Path",
    `if '--version' in sys.argv: print('phase7-${label} provider 1.0'); raise SystemExit(0)`,
    "stdin_text = sys.stdin.read()",
    "if 'CODETALK_PROBE_OK' in stdin_text:",
    "    print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'CODETALK_PROBE_OK'}}))",
    "    print(json.dumps({'type': 'turn.completed'}))",
    "    raise SystemExit(0)",
    "artifact_dir = Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
    "artifact_dir.mkdir(parents=True, exist_ok=True)",
    "time.sleep(0.35)",
    `artifact_dir.joinpath('report.md').write_text('# ${label} report\\n' + stdin_text[-120:], encoding='utf-8')`,
    "print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'phase7 fixture completed'}}))",
    "print(json.dumps({'type': 'turn.completed'}))",
    "",
  ].join("\n"), "utf8");
  const response = await request.post(`${backendBase}/api/settings/agent-runtimes`, {
    data: {
      name: `Phase 7 ${label} runtime ${stamp}`,
      provider: "codex",
      command: "python3.11",
      args: [script],
      prompt_transport: "codex_exec_json",
      output_mode: "stream_json",
      working_dir_mode: "project",
      timeout_seconds: 90,
      completion_mode: "process_exit",
      session_persistence: "none",
      requires_network: false,
      enabled: true,
    },
  });
  expect(response.status(), await response.text()).toBe(201);
  const body = await response.json() as { id: string };
  return {
    id: `agent-runtime:${body.id}`,
    remove: async () => {
      const removed = await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(body.id)}`);
      expect(removed.status()).toBe(204);
    },
  };
}

async function createWorkspace(request: APIRequestContext, name: string, repoPath: string) {
  const response = await request.post(`${backendBase}/api/workspaces`, { data: { name, repo_path: repoPath } });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json() as { id: string }).id;
}

async function createPublishedFreeSourceWorkflow(request: APIRequestContext, name: string, providerRef?: string): Promise<WorkflowFixture> {
  const created = await request.post(`${backendBase}/api/workbench/workflows/new`, {
    data: { template: "free_source_analysis", name, description: "Phase 7 V3 real-browser fixture" },
  });
  expect(created.status(), await created.text()).toBe(201);
  const body = await created.json() as { workflow: { workflow_id: string }; draft: { version_id: string; draft_revision: number; authoring_graph: { nodes: Array<Record<string, unknown>> } } };
  const graph = structuredClone(body.draft.authoring_graph);
  const agent = graph.nodes.find((node) => node.kind === "agent");
  const analysis = graph.nodes.find((node) => node.kind === "input" && node.label === "分析目标");
  expect(agent).toBeTruthy();
  expect(analysis).toBeTruthy();
  if (providerRef && agent) (agent.config as Record<string, unknown>).provider_ref = providerRef;
  const updated = await request.put(`${backendBase}/api/workbench/workflows/${body.workflow.workflow_id}/versions/${body.draft.version_id}`, {
    data: { expected_revision: body.draft.draft_revision, authoring_graph: graph },
  });
  expect(updated.ok(), await updated.text()).toBeTruthy();
  const revision = (await updated.json() as { draft_revision: number }).draft_revision;
  const published = await request.post(`${backendBase}/api/workbench/workflows/${body.workflow.workflow_id}/versions/${body.draft.version_id}/publish`, {
    data: { expected_revision: revision },
  });
  expect(published.ok(), await published.text()).toBeTruthy();
  return {
    workflowId: body.workflow.workflow_id,
    versionId: (await published.json() as { version_id: string }).version_id,
    analysisInputId: String((analysis?.config as Record<string, unknown>).input_id),
  };
}

async function createReadyTask(request: APIRequestContext, workflow: WorkflowFixture, workspaceId: string, name: string, input: string) {
  const response = await request.post(`${backendBase}/api/workbench/tasks`, {
    data: {
      name,
      description: "Phase 7 V3 isolated acceptance task",
      workspace_id: workspaceId,
      workflow_id: workflow.workflowId,
      workflow_version_id: workflow.versionId,
      lifecycle_status: "ready",
      input_values: { [workflow.analysisInputId]: input },
      tags: ["e2e", "phase7", "v3"],
    },
  });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json() as { task_id: string }).task_id;
}

async function createRun(request: APIRequestContext, taskId: string) {
  const response = await request.post(`${backendBase}/api/workbench/tasks/${taskId}/runs`, { data: {} });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json() as TaskRun).task_run_id;
}

async function executeRun(request: APIRequestContext, runId: string) {
  const response = await request.post(`${backendBase}/api/workbench/task-runs/${runId}/execute`, {
    data: { timeout_sec: 60, stop_on_error: true },
  });
  expect(response.status(), await response.text()).toBe(202);
}

async function runStatus(request: APIRequestContext, runId: string) {
  const run = await getRun(request, runId);
  return String(run.execution_status ?? run.status ?? "");
}

async function getRun(request: APIRequestContext, runId: string) {
  const response = await request.get(`${backendBase}/api/workbench/task-runs/${runId}`);
  expect(response.ok(), await response.text()).toBeTruthy();
  return await response.json() as TaskRun;
}

async function getEvents(request: APIRequestContext, runId: string) {
  const response = await request.get(`${backendBase}/api/workbench/task-runs/${runId}/events?limit=200`);
  expect(response.ok(), await response.text()).toBeTruthy();
  const body = await response.json() as { items?: Array<{ event_id: number }> } | Array<{ event_id: number }>;
  return Array.isArray(body) ? body : body.items ?? [];
}

async function getManifest(request: APIRequestContext, runId: string) {
  const response = await request.get(`${backendBase}/api/workbench/task-runs/${runId}/artifacts`);
  expect(response.ok(), await response.text()).toBeTruthy();
  const body = await response.json() as { artifacts: Array<{ audience: string; relative_path: string }> };
  return body.artifacts.filter((item) => item.audience === "deliverable");
}

function sha256(value: string) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function seedUnknownContractVersion(runId: string, version: number) {
  const dataDir = process.env.CODETALK_PLAYWRIGHT_DATA_DIR;
  expect(dataDir).toBeTruthy();
  const root = path.join(dataDir as string, "workbench", "task_runs", runId);
  for (const name of ["task_run.json", "workflow_snapshot.json", "task_bundle.json", "compiled_definition.json"]) {
    const file = path.join(root, name);
    if (!fs.existsSync(file)) continue;
    const payload = JSON.parse(fs.readFileSync(file, "utf8")) as Record<string, unknown>;
    payload.compiled_contract_version = version;
    if (name === "task_run.json") {
      for (const key of ["workflow_snapshot", "task_bundle"] as const) {
        const nested = payload[key];
        if (nested && typeof nested === "object") (nested as Record<string, unknown>).compiled_contract_version = version;
      }
    }
    fs.writeFileSync(file, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  }
}

function assertPort7100Unbound(phase: string) {
  const result = spawnSync("lsof", ["-nP", "-iTCP:7100", "-sTCP:LISTEN"], { encoding: "utf8" });
  if (result.status === 1) return;
  throw new Error(`Refusing Phase 7 E2E because real GitNexus port 7100 is bound ${phase}: ${result.stdout || result.stderr}`);
}

function evidencePath(name: string) {
  fs.mkdirSync(evidenceRoot, { recursive: true });
  return path.join(evidenceRoot, name);
}

function writeJson(name: string, payload: unknown) {
  fs.writeFileSync(evidencePath(name), `${JSON.stringify(payload, null, 2)}\n`, "utf8");
}

function uniqueStamp(testInfo: { workerIndex: number; repeatEachIndex: number }) {
  return `${Date.now()}-${testInfo.workerIndex}-${testInfo.repeatEachIndex}`;
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
