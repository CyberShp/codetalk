import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

const frontendPort = process.env.CODETALK_FRONTEND_PORT ?? "3003";
const backendPort = process.env.CODETALK_BACKEND_PORT ?? "3004";
const backendBase = `http://localhost:${backendPort}`;
const soakDurationMs = Math.max(
  1_800_000,
  Number.parseInt(process.env.CODETALK_E2E_PHASE7_SOAK_DURATION_MS ?? "1800000", 10) || 1_800_000,
);
const sampleIntervalMs = 30_000;
const interactionBudgetMs = 15_000;
const evidenceRoot = process.env.CODETALK_PHASE7_SOAK_EVIDENCE_DIR ??
  "/Volumes/Media/codetalk-e2e-artifacts/phase7/workflow-soak-local";

assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "Phase 7 real V3 workflow long soak",
});

assertGitNexusDisabled();

type TaskRunPayload = {
  task_run_id: string;
  task_id: string;
  attempt_number: number;
  execution_status?: string;
  status?: string;
  quality_status?: string;
  delivery_status?: string;
};

type TaskRunEvent = {
  event_id: number;
  event_type: string;
  payload?: Record<string, unknown>;
};

type Sample = {
  index: number;
  elapsedMs: number;
  sampledAt: string;
  status: string;
  interactionLatenciesMs: Record<string, number>;
  maxInteractionLatencyMs: number;
  taskList: ReturnType<typeof taskListMetrics> | null;
};

test("Phase 7: one real V3 Attempt stays interactive and bounded while waiting for approval for 30 minutes", async ({
  page,
  request,
}, testInfo) => {
  test.skip(
    process.env.CODETALK_E2E_PHASE7_LONG_SOAK !== "1",
    "requires CODETALK_E2E_PHASE7_LONG_SOAK=1 because this real browser soak holds one Attempt for 30 minutes",
  );
  expect(frontendPort).toBe("3233");
  expect(backendPort).toBe("3234");
  expect(process.env.CODETALK_WORKFLOW_MANAGED_TOOL_MANIFEST_DIR).toBeTruthy();
  expect(process.env.CODETALK_PLAYWRIGHT_DATA_DIR).toBeTruthy();
  test.setTimeout(soakDurationMs + 300_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  page.setDefaultTimeout(interactionBudgetMs);

  const directories = {
    api: path.join(evidenceRoot, "api"),
    logs: path.join(evidenceRoot, "logs"),
    screenshots: path.join(evidenceRoot, "screenshots"),
    trace: path.join(evidenceRoot, "trace"),
  };
  for (const directory of Object.values(directories)) fs.mkdirSync(directory, { recursive: true });
  const browserLog: Array<Record<string, unknown>> = [];
  page.on("console", (message) => {
    browserLog.push({ at: new Date().toISOString(), kind: "console", level: message.type(), text: message.text() });
  });
  page.on("pageerror", (error) => {
    browserLog.push({ at: new Date().toISOString(), kind: "pageerror", text: String(error) });
  });
  page.on("requestfailed", (requestFailure) => {
    browserLog.push({
      at: new Date().toISOString(),
      kind: "requestfailed",
      url: requestFailure.url(),
      failure: requestFailure.failure()?.errorText ?? "unknown",
    });
  });
  await page.context().tracing.start({ screenshots: true, snapshots: true, sources: true });

  const samples: Sample[] = [];
  let taskId = "";
  let taskRunId = "";
  let approvalNodeId = "";
  let terminalStatus = "not-started";
  let soakStartedAt = 0;
  let maxInteractionLatencyMs = 0;

  try {
    const stamp = Date.now();
    const workflowName = `Phase 7 workflow soak ${stamp}`;
    const fixtureRoot = path.join(evidenceRoot, "fixture");
    const repo = path.join(fixtureRoot, "repo");
    fs.mkdirSync(repo, { recursive: true });
    fs.writeFileSync(path.join(repo, "README.md"), "# Phase 7 workflow soak fixture\n", "utf8");
    const toolId = `phase7.soak-checkpoint-${stamp}`;
    writeDeterministicToolManifest(toolId);

    const workspaceId = seedIndexedWorkspaceWithoutGitNexus({ name: workflowName, repoPath: repo, stamp });
    const workspaceResponse = await request.get(`${backendBase}/api/workspaces/${workspaceId}`);
    expect(workspaceResponse.ok()).toBeTruthy();
    expect((await workspaceResponse.json()).indexed).toBe(1);

    const workflowResponse = await request.post(`${backendBase}/api/workbench/workflows/new`, {
      data: { template: "blank", name: workflowName, description: "Phase 7 real browser long soak" },
    });
    const workflowBody = await workflowResponse.json();
    expect(workflowResponse.status(), JSON.stringify(workflowBody)).toBe(201);
    const workflowId = workflowBody.workflow.workflow_id as string;
    const draftId = workflowBody.draft.version_id as string;

    const inputResponse = await request.post(
      `${backendBase}/api/workbench/workflows/${workflowId}/versions/${draftId}/nodes`,
      {
        data: {
          expected_revision: workflowBody.draft.draft_revision,
          kind: "input",
          label: "Soak input",
          position: { x: 0, y: 180 },
          config: { type: "structured_json", required: true, resolver: "manual" },
        },
      },
    );
    const inputBody = await inputResponse.json();
    expect(inputResponse.status(), JSON.stringify(inputBody)).toBe(201);
    const inputNode = inputBody.node as { id: string; config: { input_id: string }; ports: { outputs: Array<{ id: string }> } };

    const toolResponse = await request.post(
      `${backendBase}/api/workbench/workflows/${workflowId}/versions/${draftId}/nodes`,
      {
        data: {
          expected_revision: inputBody.draft.draft_revision,
          kind: "tool",
          label: "Durable soak checkpoint",
          position: { x: 320, y: 180 },
          config: { tool_id: toolId, required_permissions: ["workflow.checkpoint"], timeout_sec: 60 },
        },
      },
    );
    const toolBody = await toolResponse.json();
    expect(toolResponse.status(), JSON.stringify(toolBody)).toBe(201);
    const toolNode = toolBody.node as { id: string; ports: { inputs: Array<{ id: string }>; outputs: Array<{ id: string }> } };

    const inputEdgeResponse = await request.post(
      `${backendBase}/api/workbench/workflows/${workflowId}/versions/${draftId}/edges`,
      {
        data: {
          expected_revision: toolBody.draft.draft_revision,
          source: { node_id: inputNode.id, port_id: inputNode.ports.outputs[0].id },
          target: { node_id: toolNode.id, port_id: toolNode.ports.inputs[0].id },
        },
      },
    );
    const inputEdgeBody = await inputEdgeResponse.json();
    expect(inputEdgeResponse.status(), JSON.stringify(inputEdgeBody)).toBe(201);

    const approvalResponse = await request.post(
      `${backendBase}/api/workbench/workflows/${workflowId}/versions/${draftId}/nodes`,
      {
        data: {
          expected_revision: inputEdgeBody.draft.draft_revision,
          kind: "human_approval",
          label: "Long soak approval",
          position: { x: 640, y: 180 },
          config: { approval_timeout_sec: 7_200 },
        },
      },
    );
    const approvalBody = await approvalResponse.json();
    expect(approvalResponse.status(), JSON.stringify(approvalBody)).toBe(201);
    const approvalNode = approvalBody.node as { id: string; ports: { inputs: Array<{ id: string }> } };
    approvalNodeId = approvalNode.id;

    const approvalEdgeResponse = await request.post(
      `${backendBase}/api/workbench/workflows/${workflowId}/versions/${draftId}/edges`,
      {
        data: {
          expected_revision: approvalBody.draft.draft_revision,
          source: { node_id: toolNode.id, port_id: toolNode.ports.outputs[0].id },
          target: { node_id: approvalNode.id, port_id: approvalNode.ports.inputs[0].id },
        },
      },
    );
    const approvalEdgeBody = await approvalEdgeResponse.json();
    expect(approvalEdgeResponse.status(), JSON.stringify(approvalEdgeBody)).toBe(201);

    const publishResponse = await request.post(
      `${backendBase}/api/workbench/workflows/${workflowId}/versions/${draftId}/publish`,
      { data: { expected_revision: approvalEdgeBody.draft.draft_revision } },
    );
    expect(publishResponse.ok()).toBeTruthy();
    const versionId = (await publishResponse.json()).version_id as string;

    const taskResponse = await request.post(`${backendBase}/api/workbench/tasks`, {
      data: {
        name: workflowName,
        description: "One Attempt remains waiting_for_input during a real 30 minute browser soak.",
        workspace_id: workspaceId,
        workflow_id: workflowId,
        workflow_version_id: versionId,
        lifecycle_status: "ready",
        input_values: { [inputNode.config.input_id]: { purpose: "Phase 7 durable long-soak checkpoint" } },
        tags: ["e2e", "phase7", "workflow-soak"],
      },
    });
    expect(taskResponse.ok()).toBeTruthy();
    taskId = (await taskResponse.json()).task_id as string;

    await page.goto(`/tasks/${taskId}`, { waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: "启动新运行" }).click();
    await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}/runs/task_run_`));
    taskRunId = page.url().split("/").pop() as string;
    const runUrl = `/tasks/${taskId}/runs/${taskRunId}`;
    await expect(page.getByRole("region", { name: "人工审批" })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Attempt 1", { exact: true })).toBeVisible();
    await expect(page.getByText("等待人工审批", { exact: true }).first()).toBeVisible();
    await page.screenshot({ path: path.join(directories.screenshots, "waiting-initial.png"), fullPage: false });

    const initialRun = await readRun(request, taskRunId);
    assertWaitingAttempt(initialRun, { taskId, taskRunId });
    soakStartedAt = Date.now();
    await page.goto(`/tasks?q=${encodeURIComponent(workflowName)}`, { waitUntil: "domcontentloaded" });
    const initialListMetrics = await checkTaskList(page, workflowName);
    await page.goto(runUrl, { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("region", { name: "人工审批" })).toBeVisible();

    let sampleIndex = 0;
    for (let targetElapsedMs = sampleIntervalMs; targetElapsedMs <= soakDurationMs; targetElapsedMs += sampleIntervalMs) {
      await waitForElapsed(soakStartedAt, targetElapsedMs);
      sampleIndex += 1;
      const timings: Record<string, number> = {};
      const measure = async (name: string, action: () => Promise<void>) => {
        const startedAt = performance.now();
        await action();
        const elapsed = Math.round(performance.now() - startedAt);
        timings[name] = elapsed;
        maxInteractionLatencyMs = Math.max(maxInteractionLatencyMs, elapsed);
      };

      await measure("reloadRun", async () => {
        await page.reload({ waitUntil: "domcontentloaded", timeout: interactionBudgetMs });
        await expect(page.getByRole("region", { name: "人工审批" })).toBeVisible({ timeout: interactionBudgetMs });
      });
      await measure("toggleEventTabs", async () => {
        await page.getByRole("tab", { name: "全部事件" }).click();
        await expect(page.locator(".ct-v2-event-row").first()).toBeVisible({ timeout: interactionBudgetMs });
        await page.getByRole("tab", { name: "摘要" }).click();
        await expect(page.getByRole("region", { name: "人工审批" })).toBeVisible({ timeout: interactionBudgetMs });
      });

      let listMetrics: ReturnType<typeof taskListMetrics> | null = null;
      if (sampleIndex % 2 === 0) {
        await measure("reopenTaskList", async () => {
          await page.goto(`/tasks?q=${encodeURIComponent(workflowName)}`, { waitUntil: "domcontentloaded", timeout: interactionBudgetMs });
          listMetrics = await checkTaskList(page, workflowName, initialListMetrics);
          await page.goto(runUrl, { waitUntil: "domcontentloaded", timeout: interactionBudgetMs });
          await expect(page.getByRole("region", { name: "人工审批" })).toBeVisible({ timeout: interactionBudgetMs });
        });
      } else {
        await measure("reopenRun", async () => {
          await page.goto("about:blank", { waitUntil: "domcontentloaded", timeout: interactionBudgetMs });
          await page.goto(runUrl, { waitUntil: "domcontentloaded", timeout: interactionBudgetMs });
          await expect(page.getByRole("region", { name: "人工审批" })).toBeVisible({ timeout: interactionBudgetMs });
        });
      }

      const payload = await readRun(request, taskRunId);
      assertWaitingAttempt(payload, { taskId, taskRunId });
      const elapsedMs = Date.now() - soakStartedAt;
      samples.push({
        index: sampleIndex,
        elapsedMs,
        sampledAt: new Date().toISOString(),
        status: String(payload.execution_status ?? payload.status ?? ""),
        interactionLatenciesMs: timings,
        maxInteractionLatencyMs: Math.max(...Object.values(timings)),
        taskList: listMetrics,
      });
      writeJson(path.join(directories.api, `sample-${String(sampleIndex).padStart(2, "0")}.json`), payload);
      if (sampleIndex === 1 || targetElapsedMs + sampleIntervalMs > soakDurationMs) {
        await page.screenshot({
          path: path.join(directories.screenshots, `waiting-sample-${String(sampleIndex).padStart(2, "0")}.png`),
          fullPage: false,
        });
      }
    }

    const heldMs = Date.now() - soakStartedAt;
    expect(heldMs, "the same Attempt must remain waiting for the full Phase 7 soak duration").toBeGreaterThanOrEqual(soakDurationMs);
    await page.getByLabel("审批原因").fill("Phase 7 30 minute real browser soak approved");
    const decisionResponse = page.waitForResponse((response) => {
      return response.request().method() === "POST" && response.url().endsWith(
        `/api/workbench/task-runs/${taskRunId}/approvals/${approvalNodeId}/decision`,
      );
    });
    await page.getByRole("button", { name: "批准" }).click();
    expect((await decisionResponse).status()).toBe(202);

    await expect.poll(async () => {
      const payload = await readRun(request, taskRunId);
      return payload.execution_status ?? payload.status;
    }, { timeout: 60_000 }).toBe("completed");
    const completedRun = await readRun(request, taskRunId);
    terminalStatus = String(completedRun.execution_status ?? completedRun.status ?? "unknown");
    expect(completedRun.task_run_id).toBe(taskRunId);
    expect(completedRun.attempt_number).toBe(1);
    await expect(page.getByRole("region", { name: "人工审批" })).toHaveCount(0);
    await expect(
      page.locator(".ct-v2-run-status").filter({ hasText: "执行状态" }).getByText("已完成", { exact: true }),
    ).toBeVisible({ timeout: 30_000 });
    await page.screenshot({ path: path.join(directories.screenshots, "completed.png"), fullPage: false });

    const events = await taskRunEvents(request, taskRunId);
    const eventTypes = events.map((event) => event.event_type);
    expect(eventTypes).toContain("node_waiting");
    expect(eventTypes).toContain("human_approval_decided");
    expect(eventTypes).toContain("node_checkpoint_committed");
    expect(eventTypes).toContain("completed");
    expect(events.filter((event) => event.event_type === "node_checkpoint_committed")).toHaveLength(2);
    writeJson(path.join(directories.api, "events-final.json"), events);
    writeJson(path.join(directories.api, "run-final.json"), completedRun);
  } finally {
    await page.context().tracing.stop({ path: path.join(directories.trace, "workflow-soak.zip") });
    fs.writeFileSync(
      path.join(directories.logs, "browser-events.jsonl"),
      browserLog.map((entry) => JSON.stringify(entry)).join("\n") + (browserLog.length ? "\n" : ""),
      "utf8",
    );
    const actualDurationMs = soakStartedAt ? Date.now() - soakStartedAt : 0;
    const metrics = {
      taskId,
      taskRunId,
      approvalNodeId,
      requiredSoakDurationMs: soakDurationMs,
      actualSoakDurationMs: actualDurationMs,
      sampleCount: samples.length,
      maxInteractionLatencyMs,
      terminalStatus,
      samples,
    };
    writeJson(path.join(evidenceRoot, "metrics.json"), metrics);
    fs.writeFileSync(
      path.join(evidenceRoot, "report.md"),
      [
        "# Phase 7 Real Workflow Soak",
        "",
        `- Task: ${taskId || "not created"}`,
        `- Attempt: ${taskRunId || "not started"}`,
        `- Required hold: ${soakDurationMs} ms`,
        `- Actual hold: ${actualDurationMs} ms`,
        `- Samples: ${samples.length}`,
        `- Max browser interaction latency: ${maxInteractionLatencyMs} ms`,
        `- Terminal status: ${terminalStatus}`,
        "- Evidence: screenshots/, trace/workflow-soak.zip, logs/browser-events.jsonl, api/, metrics.json",
        "",
      ].join("\n"),
      "utf8",
    );
    await testInfo.attach("phase7-workflow-soak-metrics", { path: path.join(evidenceRoot, "metrics.json") });
  }
});

async function readRun(request: APIRequestContext, taskRunId: string): Promise<TaskRunPayload> {
  const response = await request.get(`${backendBase}/api/workbench/task-runs/${taskRunId}`);
  expect(response.ok()).toBeTruthy();
  return await response.json() as TaskRunPayload;
}

async function taskRunEvents(request: APIRequestContext, taskRunId: string): Promise<TaskRunEvent[]> {
  const response = await request.get(`${backendBase}/api/workbench/task-runs/${taskRunId}/events?limit=1000`);
  expect(response.ok()).toBeTruthy();
  const payload = await response.json() as { items?: TaskRunEvent[] };
  return payload.items ?? [];
}

function assertWaitingAttempt(payload: TaskRunPayload, identity: { taskId: string; taskRunId: string }) {
  expect(payload.task_run_id).toBe(identity.taskRunId);
  expect(payload.task_id).toBe(identity.taskId);
  expect(payload.attempt_number).toBe(1);
  expect(payload.execution_status ?? payload.status).toBe("waiting_for_input");
}

async function waitForElapsed(startedAt: number, targetElapsedMs: number) {
  const remainingMs = startedAt + targetElapsedMs - Date.now();
  if (remainingMs > 0) await new Promise((resolve) => setTimeout(resolve, remainingMs));
}

function taskListMetrics() {
  const shell = document.querySelector<HTMLElement>(".ct-v2-task-center .ct-v2-table-shell");
  const page = document.querySelector<HTMLElement>(".ct-v2-task-center");
  if (!shell || !page) throw new Error("task center shell is missing");
  const shellBox = shell.getBoundingClientRect();
  return {
    documentScrollHeight: document.documentElement.scrollHeight,
    viewportHeight: window.innerHeight,
    shellTop: Math.round(shellBox.top),
    shellBottom: Math.round(shellBox.bottom),
    shellClientHeight: shell.clientHeight,
    shellScrollHeight: shell.scrollHeight,
    pageScrollHeight: page.scrollHeight,
  };
}

async function checkTaskList(
  page: Page,
  workflowName: string,
  baseline?: ReturnType<typeof taskListMetrics>,
) {
  await expect(page.getByRole("heading", { name: "任务中心" })).toBeVisible({ timeout: interactionBudgetMs });
  await expect(page.getByRole("row").filter({ hasText: workflowName })).toBeVisible({ timeout: interactionBudgetMs });
  const metrics = await page.evaluate(taskListMetrics);
  expect(metrics.shellBottom, `task list escaped the viewport: ${JSON.stringify(metrics)}`).toBeLessThanOrEqual(
    metrics.viewportHeight + 2,
  );
  expect(metrics.documentScrollHeight).toBeLessThanOrEqual(metrics.viewportHeight + 36);
  if (baseline) {
    expect(metrics.documentScrollHeight).toBeLessThanOrEqual(baseline.documentScrollHeight + 8);
    expect(metrics.shellClientHeight).toBeLessThanOrEqual(baseline.shellClientHeight + 8);
    expect(metrics.pageScrollHeight).toBeLessThanOrEqual(baseline.pageScrollHeight + 8);
  }
  return metrics;
}

function writeDeterministicToolManifest(toolId: string) {
  const manifestDirectory = process.env.CODETALK_WORKFLOW_MANAGED_TOOL_MANIFEST_DIR;
  expect(manifestDirectory).toBeTruthy();
  fs.mkdirSync(manifestDirectory as string, { recursive: true });
  fs.writeFileSync(
    path.join(manifestDirectory as string, `${toolId}.json`),
    JSON.stringify({
      tool_id: toolId,
      implementation: "json_echo",
      input_schema: { type: "object", additionalProperties: true },
      required_permissions: ["workflow.checkpoint"],
    }),
    "utf8",
  );
}

function writeJson(target: string, value: unknown) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, JSON.stringify(value, null, 2) + "\n", "utf8");
}

function seedIndexedWorkspaceWithoutGitNexus(options: { name: string; repoPath: string; stamp: number }) {
  const dataDir = process.env.CODETALK_PLAYWRIGHT_DATA_DIR;
  expect(dataDir, "isolated Playwright data directory is required").toBeTruthy();
  const workspaceId = `phase7_soak_workspace_${options.stamp}`;
  const sqliteDb = process.env.CODETALK_PLAYWRIGHT_SQLITE_DB ?? path.join(dataDir as string, "codetalk.db");
  const seed = spawnSync(
    process.env.CODETALK_BACKEND_PYTHON ?? "python3.11",
    [
      "-c",
      [
        "import sqlite3, sys",
        "db_path, workspace_id, name, repo_path = sys.argv[1:]",
        "with sqlite3.connect(db_path) as db:",
        " db.execute(\"INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)\", (workspace_id, name, repo_path))",
      ].join("\n"),
      sqliteDb,
      workspaceId,
      options.name,
      options.repoPath,
    ],
    { encoding: "utf8" },
  );
  expect(seed.status, seed.stderr || seed.stdout).toBe(0);
  return workspaceId;
}

function assertGitNexusDisabled() {
  const required = {
    CODETALK_PLAYWRIGHT_GITNEXUS: "0",
    GITNEXUS_BIN: "/usr/bin/false",
    GITNEXUS_PORT: "7101",
    GITNEXUS_BASE_URL: "http://127.0.0.1:7101",
  } as const;
  const mismatches = Object.entries(required).filter(([name, expected]) => process.env[name] !== expected);
  if (mismatches.length) {
    throw new Error(
      "Phase 7 workflow soak must run without GitNexus. Set " +
        "CODETALK_PLAYWRIGHT_GITNEXUS=0 GITNEXUS_BIN=/usr/bin/false " +
        "GITNEXUS_PORT=7101 GITNEXUS_BASE_URL=http://127.0.0.1:7101.",
    );
  }
}
