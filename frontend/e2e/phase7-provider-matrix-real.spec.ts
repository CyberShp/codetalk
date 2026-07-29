import { createServer } from "node:http";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";
import {
  assertPhase5IsolatedRuntime,
  createWorkspaceThroughUi,
  phase5BackendBase,
  publishCurrentWorkflowThroughUi,
  selectProviderThroughUi,
  v3Axis,
} from "./support/phase5-v3-browser-helpers";

const evidenceRoot = process.env.CODETALK_E2E_ARTIFACT_DIR
  ?? "/Volumes/Media/codetalk-e2e-artifacts/phase7/provider-matrix";
const runtimeRoot = process.env.CODETALK_TEMP_DIR
  ?? "/Volumes/Media/codetalk-runtime-tmp/phase7-provider-matrix";
const builtinFixturePort = Number(process.env.CODETALK_PHASE7_BUILTIN_FIXTURE_PORT ?? "3217");
const opencodeFixturePort = Number(process.env.CODETALK_PHASE7_OPENCODE_FIXTURE_PORT ?? "3218");
const opencodeBinary = process.env.CODETALK_PHASE7_OPENCODE_BIN ?? "/opt/homebrew/bin/opencode";

assertPhase5IsolatedRuntime();
assertPhase7StorageAndRedisPolicy();
assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "Phase 7 real provider matrix browser acceptance",
});

function assertPhase7StorageAndRedisPolicy() {
  const mediaRoot = path.resolve("/Volumes/Media");
  const configuredPaths = [
    ["CODETALK_E2E_ARTIFACT_DIR", evidenceRoot],
    ["CODETALK_TEMP_DIR", runtimeRoot],
    ["CODETALK_PLAYWRIGHT_DATA_DIR", process.env.CODETALK_PLAYWRIGHT_DATA_DIR],
    ["CODETALK_PLAYWRIGHT_SQLITE_DB", process.env.CODETALK_PLAYWRIGHT_SQLITE_DB],
  ] as const;
  for (const [name, value] of configuredPaths) {
    if (!value) continue;
    const resolved = path.resolve(value);
    if (resolved !== mediaRoot && !resolved.startsWith(`${mediaRoot}${path.sep}`)) {
      throw new Error(`${name} must stay under /Volumes/Media for Phase 7 acceptance.`);
    }
  }
  const forbiddenRedis = Object.entries(process.env).find(([name, value]) =>
    name.toUpperCase().includes("REDIS")
    && /(?:^|[:=/])6399(?:\D|$)/.test(value ?? ""),
  );
  if (forbiddenRedis) {
    throw new Error(`Phase 7 acceptance must never target Redis 6399 (${forbiddenRedis[0]}).`);
  }
}

if (process.env.GITNEXUS_BIN !== "/usr/bin/false" || process.env.GITNEXUS_PORT !== "7101") {
  throw new Error(
    "Phase 7 provider matrix must run without GitNexus. " +
    "Set GITNEXUS_BIN=/usr/bin/false GITNEXUS_PORT=7101 GITNEXUS_BASE_URL=http://127.0.0.1:7101.",
  );
}

test.beforeAll(() => {
  fs.mkdirSync(evidenceRoot, { recursive: true });
  fs.mkdirSync(runtimeRoot, { recursive: true });
});

test("Builtin generic source plus goal delivers only report.md in artifact_only", async ({ page, request }, testInfo) => {
  test.setTimeout(180_000);
  const stamp = uniqueStamp(testInfo);
  const fixture = await startBuiltinModelFixture();
  const repo = createPhase7Repository(`builtin ${stamp}`);
  const workspaceName = `Phase 7 builtin workspace ${stamp}`;
  const workflowName = `Phase 7 builtin generic ${stamp}`;
  const model = await configureBuiltinModel(request, fixture.baseUrl, stamp);

  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await createWorkspaceThroughUi(page, workspaceName, repo);
    await createTemplateThroughUi(page, workflowName, "free_source_analysis");
    await requireVisibleGoalInput(page, "Builtin generic source+goal");
    await publishCurrentWorkflowThroughUi(page);
    const runId = await runPublishedMatrixWorkflowThroughUi(page, {
      workflowName,
      taskName: `Phase 7 builtin task ${stamp}`,
      workspaceName,
      fillInputs: async (inputPage) => {
        await inputPage.getByLabel("分析目标 *").fill("builtin matrix goal");
      },
      inspectOutputs: async (outputPage) => {
        await expect(outputPage.getByRole("textbox", { name: /分析报告 文件名/ })).toHaveValue("report.md");
        await expect(outputPage.getByText(/sfmea|black.box|test.activity/i)).toHaveCount(0);
      },
    });

    await assertGenericCompletion(page, request, runId, {
      provider: "builtin-llm",
      expectedGoal: "builtin matrix goal",
    });
    await captureEvidence(page, `provider-matrix-builtin-${stamp}-completed.png`, {
      fixture_requests: fixture.requests,
      run_id: runId,
    });
  } finally {
    await model.remove();
    await fixture.close();
  }
});

test("loopback Codex-compatible CLI adapter preserves verbatim goal and delivers only report.md", async ({ page, request }, testInfo) => {
  test.setTimeout(180_000);
  const stamp = uniqueStamp(testInfo);
  const repo = createPhase7Repository(`cli ${stamp}`);
  const workspaceName = `Phase 7 loopback Codex workspace ${stamp}`;
  const workflowName = `Phase 7 loopback Codex generic ${stamp}`;
  const verbatimGoal = "  First line: keep leading spaces.\n\nSecond line: preserve blank line.\nMR: https://example.invalid/phase7/42  ";
  const runtime = await configureRecordingCodexRuntime(request, repo, stamp);

  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await createWorkspaceThroughUi(page, workspaceName, repo);
    await createTemplateThroughUi(page, workflowName, "change_impact_analysis");
    await selectProviderThroughUi(page, runtime.id);
    await publishCurrentWorkflowThroughUi(page);
    const runId = await runPublishedMatrixWorkflowThroughUi(page, {
      workflowName,
      taskName: `Phase 7 loopback Codex task ${stamp}`,
      workspaceName,
      fillInputs: async (inputPage) => {
        await inputPage.getByLabel("变更说明 *").fill(verbatimGoal);
      },
      inspectOutputs: async (outputPage) => {
        await expect(outputPage.getByRole("textbox", { name: /分析报告 文件名/ })).toHaveValue("report.md");
        await expect(outputPage.getByText(/sfmea|black.box|test.activity/i)).toHaveCount(0);
      },
    });

    await assertGenericCompletion(page, request, runId, {
      provider: runtime.id,
      expectedGoal: verbatimGoal,
    });
    const received = await readArtifactJson(request, runId, /received-input\.json$/);
    const transportEnvelope = JSON.parse(String(received.rendered_user_input));
    const renderedInput = JSON.parse(String(transportEnvelope.rendered_input));
    expect(renderedInput.user_inputs).toEqual(expect.arrayContaining([
      expect.objectContaining({ value: verbatimGoal }),
    ]));
    expect(Object.values(renderedInput.resolved_inputs)).toContain(verbatimGoal);
    await captureEvidence(page, `provider-matrix-loopback-codex-${stamp}-completed.png`, {
      loopback_adapter_command: runtime.command,
      run_id: runId,
      received_input: received,
    });
  } finally {
    await runtime.remove();
  }
});

test("installed OpenCode uses an isolated approved loopback model route and delivers only report.md", async ({ page, request }, testInfo) => {
  test.setTimeout(240_000);
  const stamp = uniqueStamp(testInfo);
  const repo = createPhase7Repository(`real OpenCode ${stamp}`);
  const workspaceName = `Phase 7 real OpenCode workspace ${stamp}`;
  const workflowName = `Phase 7 real OpenCode generic ${stamp}`;
  const verbatimGoal = "  OpenCode first line.\n\nSecond line stays separate.\nURL: https://example.invalid/opencode/42  ";
  const binaryVersion = execFileSync(opencodeBinary, ["--version"], { encoding: "utf8" }).trim();
  const fixture = await startOpenCodeModelFixture();
  const runtime = await configureRealOpenCodeRuntime(request, stamp);

  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await createWorkspaceThroughUi(page, workspaceName, repo);
    await createTemplateThroughUi(page, workflowName, "change_impact_analysis");
    await selectProviderThroughUi(page, runtime.id);
    await publishCurrentWorkflowThroughUi(page);
    const runId = await runPublishedMatrixWorkflowThroughUi(page, {
      workflowName,
      taskName: `Phase 7 real OpenCode task ${stamp}`,
      workspaceName,
      fillInputs: async (inputPage) => {
        await inputPage.getByLabel("变更说明 *").fill(verbatimGoal);
      },
      inspectOutputs: async (outputPage) => {
        await expect(outputPage.getByRole("textbox", { name: /分析报告 文件名/ })).toHaveValue("report.md");
        await expect(outputPage.getByText(/sfmea|black.box|test.activity/i)).toHaveCount(0);
      },
    });

    await assertGenericCompletion(page, request, runId, {
      provider: runtime.id,
      expectedGoal: verbatimGoal,
    });
    const agentInvocation = await readArtifactJson(request, runId, /agent_runs\/[^/]+\/agent_invocation\.json$/);
    const agentTaskBundle = await readArtifactJson(request, runId, /agent_runs\/[^/]+\/task_bundle\.json$/);
    const sandboxPolicy = await readArtifactJson(request, runId, /sandbox_policy\.json$/);
    const frozenProviders = Object.values(
      (agentTaskBundle.provider_snapshot as { providers?: Record<string, Record<string, unknown>> } | undefined)
        ?.providers ?? {},
    );
    const frozenOpenCode = frozenProviders.find((provider) => provider.runtime_provider === "opencode");
    expect(JSON.stringify(agentInvocation.runtime)).toContain(opencodeBinary);
    expect(JSON.stringify(agentInvocation.runtime)).toContain("--pure");
    expect(JSON.stringify(agentInvocation.runtime)).toContain("codetalk-local/e2e-model");
    expect(frozenOpenCode).toEqual(expect.objectContaining({
      runtime_provider: "opencode",
      env_hints: expect.objectContaining({
        OPENCODE_DISABLE_AUTOUPDATE: "1",
        OPENCODE_DISABLE_TELEMETRY: "1",
      }),
    }));
    expect(JSON.stringify(frozenOpenCode)).toContain(`http://127.0.0.1:${opencodeFixturePort}/v1`);
    expect(sandboxPolicy.network_policy).toEqual(expect.objectContaining({
      mode: "intranet",
      boundary: "approved_proxy_gateway",
      allowed: true,
      approved_proxy_config_id: "phase7-opencode-loopback",
    }));
    expect(sandboxPolicy).toEqual(expect.objectContaining({
      status: "active",
      engine: "sandbox-exec",
      network: "outbound_allowed",
    }));
    expect(JSON.stringify(sandboxPolicy)).not.toContain(`${process.env.HOME}/.config/opencode`);
    expect(JSON.stringify(sandboxPolicy)).not.toContain(`${process.env.HOME}/.local/share/opencode`);
    expect(fixture.requests.length).toBeGreaterThanOrEqual(3);
    expect(fixture.requests.every((item) => item.path === "/v1/chat/completions")).toBeTruthy();
    expect(
      fixture.requests.some((fixtureRequest) => containsDecodedString(fixtureRequest, verbatimGoal)),
      "the OpenCode model request must preserve the user goal verbatim after decoding nested transport envelopes",
    ).toBeTruthy();
    await captureEvidence(page, `provider-matrix-real-opencode-${stamp}-completed.png`, {
      binary: opencodeBinary,
      binary_version: binaryVersion,
      fixture_requests: fixture.requests,
      run_id: runId,
      agent_invocation: agentInvocation,
      frozen_opencode_provider: frozenOpenCode,
      sandbox_policy: sandboxPolicy,
    });
  } finally {
    await runtime.remove();
    await fixture.close();
  }
});

test("formal storage source plus design uses visible governance and delivers flow, SFMEA, and black-box artifacts", async ({ page, request }, testInfo) => {
  test.setTimeout(180_000);
  const stamp = uniqueStamp(testInfo);
  const repo = createPhase7Repository(`formal ${stamp}`);
  const workspaceName = `Phase 7 formal workspace ${stamp}`;
  const workflowName = `Phase 7 formal storage ${stamp}`;
  const runtime = await configureFormalCodexRuntime(request, repo, stamp);
  const designPath = path.join(repo, "storage-design.md");

  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await createWorkspaceThroughUi(page, workspaceName, repo);
    await createTemplateThroughUi(page, workflowName, "formal_storage_test_design");
    await selectProviderThroughUi(page, runtime.id);
    const canvas = page.getByRole("region", { name: "工作流画布" });
    await expect(canvas.getByRole("article", { name: /存储测试设计.*governance节点/i })).toBeVisible();
    await expect(canvas.getByRole("article", { name: /SFMEA 验收.*validator节点/i })).toBeVisible();
    await expect(canvas.getByRole("article", { name: /黑盒用例验收.*validator节点/i })).toBeVisible();
    await expect(page.getByLabel("验收模式")).toHaveValue("artifact_only");
    await publishCurrentWorkflowThroughUi(page);
    const runId = await runPublishedMatrixWorkflowThroughUi(page, {
      workflowName,
      taskName: `Phase 7 formal task ${stamp}`,
      workspaceName,
      fillInputs: async (inputPage) => {
        await inputPage.getByLabel("设计文档 *").setInputFiles(designPath);
      },
      inspectOutputs: async (outputPage) => {
        await expect(outputPage.getByRole("textbox", { name: /流程说明 文件名/ })).toHaveValue("flow.md");
        await expect(outputPage.getByRole("textbox", { name: /SFMEA 风险清单 文件名/ })).toHaveValue("sfmea.json");
        await expect(outputPage.getByRole("textbox", { name: /黑盒测试用例 文件名/ })).toHaveValue("black-box-cases.json");
      },
    });

    await expect(v3Axis(page, "执行").getByText("已完成", { exact: true })).toBeVisible({ timeout: 90_000 });
    await expect(v3Axis(page, "产物校验").getByText("已通过", { exact: true })).toBeVisible();
    await expect(v3Axis(page, "专业治理").getByText("已通过", { exact: true })).toBeVisible();
    await expect(v3Axis(page, "交付").getByText("可交付", { exact: true })).toBeVisible();
    const deliverables = await deliverableNames(request, runId);
    expect(deliverables).toEqual(expect.arrayContaining(["flow.md", "sfmea.json", "black-box-cases.json"]));
    await captureEvidence(page, `provider-matrix-formal-${stamp}-completed.png`, { run_id: runId, deliverables });
  } finally {
    await runtime.remove();
  }
});

function uniqueStamp(testInfo: { workerIndex: number; repeatEachIndex: number }) {
  return `${Date.now()}-${testInfo.workerIndex}-${testInfo.repeatEachIndex}`;
}

function createPhase7Repository(label: string) {
  fs.mkdirSync(runtimeRoot, { recursive: true });
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(runtimeRoot, "provider-matrix-")));
  fs.writeFileSync(path.join(repo, "README.md"), `# ${label}\n\nPhase 7 provider-matrix fixture.\n`, "utf8");
  fs.writeFileSync(path.join(repo, "storage.c"), "int phase7_storage_flow(void) { return 7; }\n", "utf8");
  fs.writeFileSync(path.join(repo, "storage-design.md"), "# Storage design\n\nThe flow must have explicit governance.\n", "utf8");
  execFileSync("git", ["init", "-q", repo]);
  return repo;
}

async function createTemplateThroughUi(page: Page, name: string, template: string) {
  await page.goto("/workflows/new", { waitUntil: "domcontentloaded" });
  await page.getByLabel("工作流名称").fill(name);
  await expect(page.getByTestId(`workflow-template-${template}`)).toBeVisible();
  await page.getByTestId(`workflow-template-${template}`).check();
  await page.getByRole("button", { name: "创建并打开画布" }).click();
  await expect(page).toHaveURL(/\/workflows\/[^/?#]+\/designer$/, { timeout: 20_000 });
  await expect(page.getByRole("region", { name: "工作流画布" })).toBeVisible();
}

async function requireVisibleGoalInput(page: Page, matrixCase: string) {
  const canvas = page.getByRole("region", { name: "工作流画布" });
  await expect(
    canvas.getByRole("article", { name: /分析目标.*输入节点/ }),
    `${matrixCase} requires a visible, typed goal input in addition to the source workspace`,
  ).toBeVisible();
}

async function runPublishedMatrixWorkflowThroughUi(
  page: Page,
  options: {
    workflowName: string;
    taskName: string;
    workspaceName: string;
    fillInputs: (page: Page) => Promise<void>;
    inspectOutputs: (page: Page) => Promise<void>;
  },
) {
  await page.goto("/tasks/new", { waitUntil: "domcontentloaded" });
  await page.getByRole("radio", { name: new RegExp(escapeRegExp(options.workflowName)) }).check();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByRole("textbox", { name: "任务名称 *" }).fill(options.taskName);
  await page.getByLabel("工作空间 *").selectOption({ label: options.workspaceName });
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.getByRole("heading", { name: "填写本次输入" })).toBeVisible();
  await options.fillInputs(page);
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.getByRole("heading", { name: "确认执行配置" })).toBeVisible();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.getByRole("heading", { name: "确认交付输出" })).toBeVisible();
  await options.inspectOutputs(page);
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.getByRole("heading", { name: "检查并运行" })).toBeVisible();
  await page.getByRole("button", { name: "保存并运行" }).click();
  await expect(page).toHaveURL(/\/tasks\/task_[^/]+\/runs\/task_run_/, { timeout: 30_000 });
  return page.url().split("/").at(-1) ?? "";
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function assertGenericCompletion(
  page: Page,
  request: APIRequestContext,
  runId: string,
  options: { provider: string; expectedGoal: string },
) {
  await expect(v3Axis(page, "执行").getByText("已完成", { exact: true })).toBeVisible({ timeout: 90_000 });
  await expect(v3Axis(page, "产物校验").getByText("已通过", { exact: true })).toBeVisible();
  await expect(v3Axis(page, "专业治理").getByText("未请求", { exact: true })).toBeVisible();
  await expect(v3Axis(page, "交付").getByText("可交付", { exact: true })).toBeVisible();
  const deliverables = await deliverableNames(request, runId);
  expect(deliverables).toEqual(["report.md"]);
  const inputSnapshot = await readArtifactJson(request, runId, /input_snapshot\.json$/);
  const providerSnapshot = await readArtifactJson(request, runId, /provider_snapshot\.json$/);
  expect(Object.values(inputSnapshot)).toContain(options.expectedGoal);
  expect(JSON.stringify(providerSnapshot)).toContain(options.provider);
}

async function deliverableNames(request: APIRequestContext, runId: string) {
  const response = await request.get(`${phase5BackendBase}/api/workbench/task-runs/${runId}/artifacts`);
  expect(response.ok(), await response.text()).toBeTruthy();
  const manifest = await response.json() as { artifacts: Array<{ audience: string; relative_path: string }> };
  return manifest.artifacts
    .filter((artifact) => artifact.audience === "deliverable")
    .map((artifact) => path.basename(artifact.relative_path))
    .sort();
}

async function readArtifactJson(request: APIRequestContext, runId: string, pattern: RegExp) {
  const response = await request.get(`${phase5BackendBase}/api/workbench/task-runs/${runId}/artifacts`);
  expect(response.ok(), await response.text()).toBeTruthy();
  const manifest = await response.json() as { artifacts: Array<{ relative_path: string }> };
  const relativePath = manifest.artifacts.map((artifact) => artifact.relative_path).find((candidate) => pattern.test(candidate));
  expect(relativePath, `artifact matching ${pattern} must exist`).toBeTruthy();
  const content = await request.get(
    `${phase5BackendBase}/api/workbench/task-runs/${runId}/artifacts/content/${relativePath}`,
  );
  expect(content.ok(), await content.text()).toBeTruthy();
  const payload = await content.json() as { content: string };
  return JSON.parse(payload.content) as Record<string, unknown>;
}

async function captureEvidence(page: Page, fileName: string, payload: Record<string, unknown>) {
  await page.screenshot({ path: path.join(evidenceRoot, fileName), fullPage: false });
  fs.writeFileSync(
    path.join(evidenceRoot, fileName.replace(/\.png$/, ".json")),
    JSON.stringify(payload, null, 2),
    "utf8",
  );
}

async function configureBuiltinModel(request: APIRequestContext, baseUrl: string, stamp: string) {
  const created = await request.post(`${phase5BackendBase}/api/settings/llm`, {
    data: {
      name: `Phase 7 loopback builtin ${stamp}`,
      api_type: "openai_compat",
      base_url: baseUrl,
      api_key: "phase7-local-only",
      model: "phase7-fixture",
      max_tokens: 1024,
      temperature: 0,
    },
  });
  expect(created.status(), await created.text()).toBe(201);
  const model = await created.json() as { id: string };
  const general = await request.get(`${phase5BackendBase}/api/settings/general`);
  expect(general.ok(), await general.text()).toBeTruthy();
  const existing = await general.json() as Record<string, unknown>;
  const updated = await request.put(`${phase5BackendBase}/api/settings/general`, {
    data: { ...existing, active_chat_model_id: model.id },
  });
  expect(updated.ok(), await updated.text()).toBeTruthy();
  return {
    remove: async () => {
      const cleared = await request.put(`${phase5BackendBase}/api/settings/general`, {
        data: { ...existing, active_chat_model_id: String(existing.active_chat_model_id ?? "") },
      });
      expect(cleared.ok(), await cleared.text()).toBeTruthy();
      const removed = await request.delete(`${phase5BackendBase}/api/settings/llm/${model.id}`);
      expect(removed.status()).toBe(204);
    },
  };
}

async function configureRecordingCodexRuntime(request: APIRequestContext, repo: string, stamp: string) {
  const script = path.join(repo, `phase7-loopback-codex-${stamp}.py`);
  fs.writeFileSync(script, [
    "import json, os, sys",
    "from pathlib import Path",
    "if '--version' in sys.argv: print('phase7-loopback-codex 1.0'); raise SystemExit(0)",
    "stdin_text = sys.stdin.read()",
    "if 'CODETALK_PROBE_OK' in stdin_text:",
    "    print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'CODETALK_PROBE_OK'}}))",
    "    print(json.dumps({'type': 'turn.completed'}))",
    "    raise SystemExit(0)",
    "artifact_dir = Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
    "artifact_dir.mkdir(parents=True, exist_ok=True)",
    "(artifact_dir / 'received-input.json').write_text(json.dumps({'rendered_user_input': stdin_text}, ensure_ascii=False), encoding='utf-8')",
    "(artifact_dir / 'report.md').write_text('# Phase 7 loopback Codex-compatible adapter report\\n', encoding='utf-8')",
    "print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'phase7 loopback Codex-compatible adapter completed'}}))",
    "print(json.dumps({'type': 'turn.completed'}))",
    "",
  ].join("\n"), "utf8");
  return configureRuntime(request, `Phase 7 loopback Codex-compatible ${stamp}`, script);
}

async function configureFormalCodexRuntime(request: APIRequestContext, repo: string, stamp: string) {
  const script = path.join(repo, `phase7-formal-${stamp}.py`);
  fs.writeFileSync(script, [
    "import hashlib, json, os, sys",
    "from pathlib import Path",
    "if '--version' in sys.argv: print('phase7-formal 1.0'); raise SystemExit(0)",
    "stdin_text = sys.stdin.read()",
    "if 'CODETALK_PROBE_OK' in stdin_text:",
    "    print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'CODETALK_PROBE_OK'}}))",
    "    print(json.dumps({'type': 'turn.completed'}))",
    "    raise SystemExit(0)",
    "artifact_dir = Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
    "artifact_dir.mkdir(parents=True, exist_ok=True)",
    "source = Path(os.environ.get('CODETALK_PROJECT_ROOT', '.')) / 'storage.c'",
    "source_text = source.read_text(encoding='utf-8')",
    "evidence = [{'file_path': 'storage.c', 'start_line': 1, 'end_line': 1, 'excerpt': source_text.rstrip(), 'symbols': ['phase7_storage_flow'], 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}]",
    "(artifact_dir / 'flow.md').write_text('# Storage flow\\n', encoding='utf-8')",
    "(artifact_dir / 'source-evidence.json').write_text(json.dumps(evidence), encoding='utf-8')",
    "print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'phase7 formal completed'}}))",
    "print(json.dumps({'type': 'turn.completed'}))",
    "",
  ].join("\n"), "utf8");
  return configureRuntime(request, `Phase 7 formal Codex ${stamp}`, script);
}

async function configureRuntime(request: APIRequestContext, name: string, script: string) {
  const created = await request.post(`${phase5BackendBase}/api/settings/agent-runtimes`, {
    data: {
      name,
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
  expect(created.status(), await created.text()).toBe(201);
  const runtime = await created.json() as { id: string };
  return {
    id: `agent-runtime:${runtime.id}`,
    command: "python3.11",
    remove: async () => {
      const removed = await request.delete(`${phase5BackendBase}/api/settings/agent-runtimes/${runtime.id}`);
      expect(removed.status()).toBe(204);
    },
  };
}

async function configureRealOpenCodeRuntime(request: APIRequestContext, stamp: string) {
  const inlineConfig = JSON.stringify({
    share: "disabled",
    enabled_providers: ["codetalk-local"],
    provider: {
      "codetalk-local": {
        npm: "@ai-sdk/openai-compatible",
        name: "CodeTalk local E2E",
        options: { baseURL: `http://127.0.0.1:${opencodeFixturePort}/v1` },
        models: { "e2e-model": { name: "CodeTalk E2E Model" } },
      },
    },
  });
  const created = await request.post(`${phase5BackendBase}/api/settings/agent-runtimes`, {
    data: {
      name: `Phase 7 installed OpenCode ${stamp}`,
      provider: "opencode",
      command: opencodeBinary,
      args: ["--pure", "--model", "codetalk-local/e2e-model"],
      prompt_transport: "opencode_run_arg",
      output_mode: "auto",
      working_dir_mode: "project",
      env: {
        OPENCODE_CONFIG_CONTENT: inlineConfig,
        OPENCODE_DISABLE_AUTOUPDATE: "1",
        OPENCODE_DISABLE_TELEMETRY: "1",
      },
      timeout_seconds: 120,
      completion_mode: "process_exit",
      session_persistence: "none",
      requires_network: true,
      enabled: true,
    },
  });
  expect(created.status(), await created.text()).toBe(201);
  const runtime = await created.json() as { id: string };
  return {
    id: `agent-runtime:${runtime.id}`,
    remove: async () => {
      const removed = await request.delete(`${phase5BackendBase}/api/settings/agent-runtimes/${runtime.id}`);
      expect(removed.status()).toBe(204);
    },
  };
}

async function startBuiltinModelFixture() {
  const requests: Array<Record<string, unknown>> = [];
  const server = createServer((incoming, outgoing) => {
    const chunks: Buffer[] = [];
    incoming.on("data", (chunk: Buffer) => chunks.push(chunk));
    incoming.on("end", () => {
      requests.push({ url: incoming.url, body: JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}") });
      outgoing.writeHead(200, { "content-type": "application/json" });
      outgoing.end(JSON.stringify({
        model: "phase7-fixture",
        choices: [{ message: { content: "# Phase 7 builtin report\\n\\nOnly report.md is declared." }, finish_reason: "stop" }],
        usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
      }));
    });
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(builtinFixturePort, "127.0.0.1", () => resolve());
  });
  return {
    baseUrl: `http://127.0.0.1:${builtinFixturePort}`,
    requests,
    close: async () => new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve())),
  };
}

async function startOpenCodeModelFixture() {
  const requests: Array<Record<string, unknown>> = [];
  const server = createServer((incoming, outgoing) => {
    const chunks: Buffer[] = [];
    incoming.on("data", (chunk: Buffer) => chunks.push(chunk));
    incoming.on("end", () => {
      const rawUrl = incoming.url ?? "";
      const requestUrl = new URL(rawUrl, `http://127.0.0.1:${opencodeFixturePort}`);
      const payload = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}") as {
        messages?: Array<{ role?: string; content?: unknown }>;
        tools?: unknown[];
      };
      requests.push({ raw_url: rawUrl, path: requestUrl.pathname, payload });
      if (requestUrl.pathname !== "/v1/chat/completions") {
        outgoing.writeHead(404).end();
        return;
      }
      const messages = Array.isArray(payload.messages) ? payload.messages : [];
      const tools = Array.isArray(payload.tools) ? payload.tools : [];
      const messageText = messages.map((message) =>
        typeof message.content === "string" ? message.content : JSON.stringify(message.content ?? ""),
      ).join("\n");
      const messageHistory = JSON.stringify(messages);
      const artifactDir = messageText.match(/"artifact_dir"\s*:\s*"([^"]+)"/)?.[1]
        ?? messageHistory.match(/\\"artifact_dir\\"\s*:\s*\\"([^"\\]+)\\"/)?.[1];
      const hasBashCall = messageHistory.includes('"name":"bash"');
      const hasWriteCall = messageHistory.includes('"name":"write"');
      const responseChunks: Array<Record<string, unknown>> = [];
      if (tools.length > 0 && artifactDir && !hasWriteCall) {
        responseChunks.push(openCodeChunk({
          role: "assistant",
          tool_calls: [{
            index: 0,
            id: "call_codetalk_write_report",
            type: "function",
            function: {
              name: "write",
              arguments: JSON.stringify({
                filePath: path.join(artifactDir, "report.md"),
                content: "# Phase 7 real OpenCode report\n\nGenerated by the installed OpenCode CLI through its write tool.\n",
              }),
            },
          }],
        }));
        responseChunks.push(openCodeChunk({}, "tool_calls"));
      } else if (tools.length > 0 && !hasBashCall) {
        responseChunks.push(openCodeChunk({
          role: "assistant",
          tool_calls: [{
            index: 0,
            id: "call_codetalk_read_task",
            type: "function",
            function: {
              name: "bash",
              arguments: JSON.stringify({
                command: 'cat "$CODETALK_AGENT_PROMPT_FILE"',
                description: "Read the complete CodeTalk task file",
              }),
            },
          }],
        }));
        responseChunks.push(openCodeChunk({}, "tool_calls"));
      } else {
        responseChunks.push(openCodeChunk({
          role: "assistant",
          content: tools.length === 0 ? "CodeTalk OpenCode E2E" : "LOCAL_OPENCODE_OK",
        }));
        responseChunks.push(openCodeChunk({}, "stop"));
      }
      outgoing.writeHead(200, { "content-type": "text/event-stream", connection: "close" });
      for (const chunk of responseChunks) outgoing.write(`data: ${JSON.stringify(chunk)}\n\n`);
      outgoing.end("data: [DONE]\n\n");
    });
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(opencodeFixturePort, "127.0.0.1", () => resolve());
  });
  return {
    requests,
    close: async () => new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve())),
  };
}

function openCodeChunk(delta: Record<string, unknown>, finishReason: string | null = null) {
  return {
    id: "chatcmpl-codetalk-local",
    object: "chat.completion.chunk",
    created: Math.floor(Date.now() / 1000),
    model: "e2e-model",
    choices: [{ index: 0, delta, finish_reason: finishReason }],
  };
}

function containsDecodedString(value: unknown, expected: string, depth = 0): boolean {
  if (depth > 12) return false;
  if (typeof value === "string") {
    if (value.includes(expected)) return true;
    try {
      return containsDecodedString(JSON.parse(value), expected, depth + 1);
    } catch {
      return false;
    }
  }
  if (Array.isArray(value)) {
    return value.some((item) => containsDecodedString(item, expected, depth + 1));
  }
  if (value && typeof value === "object") {
    return Object.values(value).some((item) => containsDecodedString(item, expected, depth + 1));
  }
  return false;
}
