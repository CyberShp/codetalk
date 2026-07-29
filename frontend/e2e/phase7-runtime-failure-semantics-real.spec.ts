import { expect, test, type APIRequestContext } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

const frontendPort = process.env.CODETALK_FRONTEND_PORT ?? "3003";
const backendPort = process.env.CODETALK_BACKEND_PORT ?? "3004";
const backendBase = `http://localhost:${backendPort}`;
const evidenceRoot = process.env.CODETALK_E2E_ARTIFACT_DIR
  ?? "/Volumes/Media/codetalk-e2e-artifacts/phase7/runtime-failure-semantics";
const tempRoot = process.env.CODETALK_TEMP_DIR
  ?? "/Volumes/Media/codetalk-runtime-tmp/phase7-runtime-failure-semantics";

assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "Phase 7 real browser runtime failure semantics",
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
  throw new Error(
    "Phase 7 failure semantics must use isolated 3233/3234 servers with GitNexus disabled.",
  );
}

type WorkflowFixture = {
  workflowId: string;
  versionId: string;
  analysisInputId: string;
};

type TaskRun = {
  task_run_id: string;
  execution_status?: string;
  status?: string;
  artifact_dir?: string;
  run_ui_summary?: Record<string, unknown>;
};

test.beforeAll(() => {
  fs.mkdirSync(evidenceRoot, { recursive: true });
  fs.mkdirSync(tempRoot, { recursive: true });
});

test("Phase 7: bad source path has actionable Chinese UI guidance and creates no workspace", async ({ page, request }, testInfo) => {
  test.setTimeout(60_000);
  const stamp = uniqueStamp(testInfo);
  const before = await workspaceNames(request);

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
  await page.getByLabel("工作空间名称").fill(`Phase 7 bad path ${stamp}`);
  await page.getByLabel("代码仓库路径").fill(`/Volumes/Media/does-not-exist-${stamp}`);
  await page.getByRole("button", { name: "创建工作空间" }).click();

  const alert = page.getByRole("alert").filter({ hasText: "代码路径不存在" });
  await expect(alert).toContainText("代码路径不存在");
  await expect(alert).toContainText("修复建议：请确认路径拼写、挂载点和权限");
  await expect(alert).toContainText("/Volumes/");
  await page.screenshot({ path: evidencePath(`bad-source-path-${stamp}.png`), fullPage: true });

  expect(await workspaceNames(request)).toEqual(before);
  writeJson(`bad-source-path-${stamp}.json`, {
    message: await alert.innerText(),
    workspace_names_before: before,
    workspace_names_after: await workspaceNames(request),
  });
});

test("Phase 7: bad file is rejected before scheduling and unavailable provider is actionable with no report", async ({ page, request }, testInfo) => {
  test.setTimeout(120_000);
  const stamp = uniqueStamp(testInfo);
  const repo = createRepository(`bad-file-provider-${stamp}`);
  const workspaceId = await createWorkspace(request, `Phase 7 provider workspace ${stamp}`, repo);
  const unavailable = await configureRuntime(request, repo, stamp, "unavailable");
  const workflow = await createFreeSourceWorkflow(request, {
    name: `Phase 7 unavailable provider ${stamp}`,
    providerRef: unavailable.id,
    idleTimeoutSec: 2,
    timeoutSec: 30,
  });

  try {
    const badFileBody = await rejectMissingDesignFileBeforeTrial(
      request,
      workspaceId,
      repo,
      stamp,
    );

    const taskId = await createReadyTask(request, workflow, workspaceId, `Phase 7 unavailable provider task ${stamp}`);
    await page.goto(`/tasks/${taskId}`, { waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: "启动新运行" }).click();
    await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}/runs/task_run_`));
    const runId = page.url().split("/").at(-1) ?? "";
    await expect.poll(() => taskRunStatus(request, runId), { timeout: 30_000 }).toBe("failed");
    await expect(page.getByRole("heading", { name: "执行器启动前检查未通过" })).toBeVisible();
    await expect(
      page.locator("strong").filter({ hasText: "所选 Agent 未通过启动前可用性检查" }),
    ).toBeVisible();
    await expect(page.getByRole("link", { name: "检查执行器设置" })).toBeVisible();
    await expect(page.locator(".ct-v2-run-deliverables")).toContainText("0 个待修复草稿");
    const artifactNames = await deliverableNames(request, runId);
    expect(artifactNames).toEqual([]);
    await page.screenshot({ path: evidencePath(`unavailable-provider-${stamp}.png`), fullPage: true });
    writeJson(`bad-file-and-unavailable-provider-${stamp}.json`, {
      bad_file_response: badFileBody,
      task_run_id: runId,
      terminal_status: await taskRunStatus(request, runId),
      deliverables: artifactNames,
    });
  } finally {
    await unavailable.remove();
  }
});

test("Phase 7: browser cancellation, idle timeout, and total timeout keep report.md out of delivery", async ({ page, request }, testInfo) => {
  test.setTimeout(180_000);
  const stamp = uniqueStamp(testInfo);
  const repo = createRepository(`runtime-timeouts-${stamp}`);
  const workspaceId = await createWorkspace(request, `Phase 7 timeout workspace ${stamp}`, repo);
  const hanging = await configureRuntime(request, repo, stamp, "hang");

  try {
    const cancellationWorkflow = await createFreeSourceWorkflow(request, {
      name: `Phase 7 cancellation ${stamp}`,
      providerRef: hanging.id,
      idleTimeoutSec: 30,
      timeoutSec: 90,
    });
    const cancellationTask = await createReadyTask(
      request,
      cancellationWorkflow,
      workspaceId,
      `Phase 7 cancellation task ${stamp}`,
    );
    await page.goto(`/tasks/${cancellationTask}`, { waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: "启动新运行" }).click();
    await expect(page).toHaveURL(new RegExp(`/tasks/${cancellationTask}/runs/task_run_`));
    const cancellationRun = page.url().split("/").at(-1) ?? "";
    await expect.poll(() => taskRunStatus(request, cancellationRun), { timeout: 30_000 }).toBe("running");
    await page.getByRole("button", { name: "取消" }).click();
    await expect.poll(() => taskRunStatus(request, cancellationRun), { timeout: 30_000 }).toBe("cancelled");
    await expect(page.getByText("已取消", { exact: true }).first()).toBeVisible();
    expect(await deliverableNames(request, cancellationRun)).toEqual([]);
    await page.screenshot({ path: evidencePath(`cancelled-${stamp}.png`), fullPage: true });

    const idleWorkflow = await createFreeSourceWorkflow(request, {
      name: `Phase 7 idle timeout ${stamp}`,
      providerRef: hanging.id,
      idleTimeoutSec: 2,
      timeoutSec: 30,
    });
    const idleTask = await createReadyTask(request, idleWorkflow, workspaceId, `Phase 7 idle task ${stamp}`);
    const idleRun = await createAndExecuteRun(request, idleTask, 0);
    await page.goto(`/tasks/${idleTask}/runs/${idleRun}`, { waitUntil: "domcontentloaded" });
    await expect.poll(() => taskRunStatus(request, idleRun), { timeout: 45_000 }).toBe("failed");
    const idleEvidence = await runEvidence(request, idleRun);
    expect(JSON.stringify(idleEvidence.events)).toMatch(/连续 2s 没有输出或进度/);
    await expect(page.getByText(/Agent 执行超时|没有输出或进度/i).first()).toBeVisible();
    expect(await deliverableNames(request, idleRun)).toEqual([]);
    await page.screenshot({ path: evidencePath(`idle-timeout-${stamp}.png`), fullPage: true });

    const totalWorkflow = await createFreeSourceWorkflow(request, {
      name: `Phase 7 total timeout ${stamp}`,
      providerRef: hanging.id,
      idleTimeoutSec: 30,
      timeoutSec: 90,
    });
    const totalTask = await createReadyTask(request, totalWorkflow, workspaceId, `Phase 7 total task ${stamp}`);
    const totalRun = await createAndExecuteRun(request, totalTask, 2);
    await page.goto(`/tasks/${totalTask}/runs/${totalRun}`, { waitUntil: "domcontentloaded" });
    await expect.poll(() => taskRunStatus(request, totalRun), { timeout: 45_000 }).toBe("failed");
    const totalEvidence = await runEvidence(request, totalRun);
    expect(JSON.stringify(totalEvidence.events)).toMatch(/安全运行上限|总超时|total.*timed out/i);
    await expect(
      page.getByText("Agent 执行超时，当前节点还没有产出可交付结果。", { exact: true }),
    ).toBeVisible();
    expect(await deliverableNames(request, totalRun)).toEqual([]);
    await page.screenshot({ path: evidencePath(`total-timeout-${stamp}.png`), fullPage: true });

    writeJson(`cancellation-and-timeouts-${stamp}.json`, {
      cancellation: await runEvidence(request, cancellationRun),
      idle_timeout: idleEvidence,
      total_timeout: totalEvidence,
    });
  } finally {
    await hanging.remove();
  }
});

function uniqueStamp(testInfo: { titlePath: string[] }) {
  return `${Date.now()}-${testInfo.titlePath.at(-1)?.replace(/[^a-z0-9]+/gi, "-").slice(0, 18) ?? "phase7"}`;
}

function evidencePath(name: string) {
  fs.mkdirSync(evidenceRoot, { recursive: true });
  return path.join(evidenceRoot, name);
}

function writeJson(name: string, payload: unknown) {
  fs.writeFileSync(evidencePath(name), `${JSON.stringify(payload, null, 2)}\n`, "utf8");
}

function createRepository(label: string) {
  const repo = fs.mkdtempSync(path.join(tempRoot, "phase7-runtime-"));
  fs.writeFileSync(path.join(repo, "README.md"), `# ${label}\n`, "utf8");
  fs.writeFileSync(path.join(repo, "source.c"), "int phase7_runtime_fixture(void) { return 7; }\n", "utf8");
  execFileSync("git", ["init", "-q", repo]);
  return repo;
}

async function workspaceNames(request: APIRequestContext) {
  const response = await request.get(`${backendBase}/api/workspaces`);
  expect(response.ok(), await response.text()).toBeTruthy();
  const body = await response.json() as { items?: Array<{ name: string }> } | Array<{ name: string }>;
  const items = Array.isArray(body) ? body : body.items ?? [];
  return items.map((item) => item.name).sort();
}

async function createWorkspace(request: APIRequestContext, name: string, repoPath: string) {
  const response = await request.post(`${backendBase}/api/workspaces`, { data: { name, repo_path: repoPath } });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json() as { id: string }).id;
}

async function configureRuntime(
  request: APIRequestContext,
  repo: string,
  stamp: string,
  mode: "hang" | "unavailable",
) {
  const script = path.join(repo, `phase7-runtime-${mode}-${stamp}.py`);
  const lines = mode === "unavailable"
    ? [
        "import sys",
        "if '--version' in sys.argv: print('unavailable fixture', file=sys.stderr); raise SystemExit(9)",
        "raise SystemExit(9)",
      ]
    : [
        "import json, sys, time",
        "if '--version' in sys.argv: print('phase7-hanging-provider 1.0'); raise SystemExit(0)",
        "stdin_text = sys.stdin.read()",
        "if 'CODETALK_PROBE_OK' in stdin_text:",
        "    print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'CODETALK_PROBE_OK'}}))",
        "    print(json.dumps({'type': 'turn.completed'}))",
        "    raise SystemExit(0)",
        "time.sleep(90)",
      ];
  fs.writeFileSync(script, `${lines.join("\n")}\n`, "utf8");
  const response = await request.post(`${backendBase}/api/settings/agent-runtimes`, {
    data: {
      name: `Phase 7 ${mode} runtime ${stamp}`,
      provider: "codex",
      command: "python3.11",
      args: [script],
      prompt_transport: "codex_exec_json",
      output_mode: "stream_json",
      working_dir_mode: "project",
      timeout_seconds: 120,
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
      const removed = await request.delete(`${backendBase}/api/settings/agent-runtimes/${runtime.id}`);
      expect(removed.status()).toBe(204);
    },
  };
}

async function createFreeSourceWorkflow(
  request: APIRequestContext,
  options: { name: string; providerRef: string; idleTimeoutSec: number; timeoutSec: number },
): Promise<WorkflowFixture> {
  const created = await request.post(`${backendBase}/api/workbench/workflows/new`, {
    data: { template: "free_source_analysis", name: options.name, description: "Phase 7 local failure semantics" },
  });
  expect(created.status(), await created.text()).toBe(201);
  const body = await created.json() as {
    workflow: { workflow_id: string };
    draft: { version_id: string; draft_revision: number; authoring_graph: { nodes: Array<Record<string, unknown>> } };
  };
  const graph = structuredClone(body.draft.authoring_graph);
  const nodes = graph.nodes;
  const agent = nodes.find((node) => node.kind === "agent");
  const analysis = nodes.find((node) => node.kind === "input" && node.label === "分析目标");
  expect(agent).toBeTruthy();
  expect(analysis).toBeTruthy();
  const config = (agent?.config ?? {}) as Record<string, unknown>;
  config.provider_ref = options.providerRef;
  config.idle_timeout_sec = options.idleTimeoutSec;
  config.timeout_sec = options.timeoutSec;
  const updated = await request.put(
    `${backendBase}/api/workbench/workflows/${body.workflow.workflow_id}/versions/${body.draft.version_id}`,
    { data: { expected_revision: body.draft.draft_revision, authoring_graph: graph } },
  );
  expect(updated.ok(), await updated.text()).toBeTruthy();
  const updatedBody = await updated.json() as { draft_revision: number };
  const published = await request.post(
    `${backendBase}/api/workbench/workflows/${body.workflow.workflow_id}/versions/${body.draft.version_id}/publish`,
    { data: { expected_revision: updatedBody.draft_revision } },
  );
  expect(published.ok(), await published.text()).toBeTruthy();
  return {
    workflowId: body.workflow.workflow_id,
    versionId: (await published.json() as { version_id: string }).version_id,
    analysisInputId: String((analysis?.config as Record<string, unknown>).input_id),
  };
}

async function rejectMissingDesignFileBeforeTrial(
  request: APIRequestContext,
  workspaceId: string,
  repo: string,
  stamp: string,
) {
  const created = await request.post(`${backendBase}/api/workbench/workflows/new`, {
    data: {
      template: "source_with_optional_design",
      name: `Phase 7 bad file ${stamp}`,
      description: "Phase 7 missing design-file preflight",
    },
  });
  expect(created.status(), await created.text()).toBe(201);
  const body = await created.json() as {
    workflow: { workflow_id: string };
    draft: {
      version_id: string;
      draft_revision: number;
      authoring_graph: { nodes: Array<Record<string, unknown>> };
    };
  };
  const source = body.draft.authoring_graph.nodes.find(
    (node) => node.kind === "input" && (node.config as Record<string, unknown>).type === "directory",
  );
  const design = body.draft.authoring_graph.nodes.find(
    (node) => node.kind === "input" && (node.config as Record<string, unknown>).type === "file",
  );
  expect(source).toBeTruthy();
  expect(design).toBeTruthy();
  const missingPath = `/Volumes/Media/missing-input-${stamp}.md`;
  const countBefore = taskRunDirectoryCount();
  const failed = await request.post(
    `${backendBase}/api/workbench/workflows/${body.workflow.workflow_id}`
      + `/versions/${body.draft.version_id}/test-run`,
    {
      data: {
        workspace_id: workspaceId,
        expected_revision: body.draft.draft_revision,
        inputs: {
          [String((source?.config as Record<string, unknown>).input_id)]: repo,
          [String((design?.config as Record<string, unknown>).input_id)]: missingPath,
        },
      },
    },
  );
  expect(failed.status(), await failed.text()).toBe(422);
  const detail = await failed.json() as { detail?: string };
  expect(String(detail.detail)).toContain(missingPath);
  expect(taskRunDirectoryCount()).toBe(countBefore);
  return detail;
}

async function createReadyTask(
  request: APIRequestContext,
  workflow: WorkflowFixture,
  workspaceId: string,
  name: string,
) {
  const response = await request.post(`${backendBase}/api/workbench/tasks`, {
    data: {
      name,
      description: "Phase 7 local browser failure semantics",
      workspace_id: workspaceId,
      workflow_id: workflow.workflowId,
      workflow_version_id: workflow.versionId,
      lifecycle_status: "ready",
      input_values: { [workflow.analysisInputId]: "验证本地失败语义" },
      tags: ["e2e", "phase7", "failure-semantics"],
    },
  });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json() as { task_id: string }).task_id;
}

async function createAndExecuteRun(request: APIRequestContext, taskId: string, timeoutSec: number) {
  const attempt = await request.post(`${backendBase}/api/workbench/tasks/${taskId}/runs`, { data: {} });
  expect(attempt.status(), await attempt.text()).toBe(201);
  const runId = (await attempt.json() as { task_run_id: string }).task_run_id;
  const execute = await request.post(`${backendBase}/api/workbench/task-runs/${runId}/execute`, {
    data: { timeout_sec: timeoutSec, stop_on_error: true },
  });
  expect(execute.status(), await execute.text()).toBe(202);
  return runId;
}

async function taskRunStatus(request: APIRequestContext, runId: string) {
  const response = await request.get(`${backendBase}/api/workbench/task-runs/${runId}`);
  expect(response.ok(), await response.text()).toBeTruthy();
  const run = await response.json() as TaskRun;
  return String(run.execution_status ?? run.status ?? "");
}

async function deliverableNames(request: APIRequestContext, runId: string) {
  const response = await request.get(`${backendBase}/api/workbench/task-runs/${runId}/artifacts`);
  expect(response.ok(), await response.text()).toBeTruthy();
  const manifest = await response.json() as { artifacts: Array<{ audience: string; relative_path: string }> };
  return manifest.artifacts.filter((artifact) => artifact.audience === "deliverable").map((artifact) => artifact.relative_path).sort();
}

async function runEvidence(request: APIRequestContext, runId: string) {
  const runResponse = await request.get(`${backendBase}/api/workbench/task-runs/${runId}`);
  expect(runResponse.ok()).toBeTruthy();
  const eventsResponse = await request.get(`${backendBase}/api/workbench/task-runs/${runId}/events?limit=200`);
  expect(eventsResponse.ok()).toBeTruthy();
  return {
    run: await runResponse.json(),
    deliverables: await deliverableNames(request, runId),
    events: await eventsResponse.json(),
  };
}

function taskRunDirectoryCount() {
  const dataDir = process.env.CODETALK_PLAYWRIGHT_DATA_DIR;
  expect(dataDir).toBeTruthy();
  const root = path.join(dataDir as string, "workbench", "task_runs");
  return fs.existsSync(root) ? fs.readdirSync(root).filter((name) => name.startsWith("task_run_")).length : 0;
}
