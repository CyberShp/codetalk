import { expect, test } from "@playwright/test";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

const frontendPort = process.env.CODETALK_FRONTEND_PORT ?? "3003";
const backendPort = process.env.CODETALK_BACKEND_PORT ?? "3004";
const backendBase = `http://localhost:${backendPort}`;
const evidenceRoot =
  process.env.CODETALK_PHASE6_EVIDENCE_DIR ??
  "/Volumes/Media/codetalk-e2e-artifacts/phase6-checkpoint-hitl-real";

assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "Phase 6 checkpoint and HITL recovery",
});

test("restores one waiting Attempt and resumes it after a real approval", async ({
  page,
  request,
}) => {
  assertPhase6RestartRuntime();
  test.setTimeout(180_000);
  await page.setViewportSize({ width: 1440, height: 900 });

  const stamp = Date.now();
  const workflowName = `Phase 6 HITL E2E ${stamp}`;
  const fixtureRoot = path.join(evidenceRoot, `fixture-${stamp}`);
  const repo = path.join(fixtureRoot, "repo");
  fs.mkdirSync(repo, { recursive: true });
  fs.writeFileSync(path.join(repo, "README.md"), "# Phase 6 HITL recovery\n", "utf8");
  const toolId = `phase6.checkpoint-echo-${stamp}`;
  writeDeterministicToolManifest(toolId);

  const workspaceResponse = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workflowName, repo_path: repo },
  });
  expect(workspaceResponse.status()).toBe(201);
  const workspaceId = (await workspaceResponse.json()).id as string;

  const workflowResponse = await request.post(`${backendBase}/api/workbench/workflows/new`, {
    data: {
      template: "blank",
      name: workflowName,
      description: "真实人工审批暂停与恢复",
    },
  });
  const workflowBody = await workflowResponse.json();
  expect(workflowResponse.status(), JSON.stringify(workflowBody)).toBe(201);
  const workflowId = workflowBody.workflow.workflow_id as string;
  const draftId = workflowBody.draft.version_id as string;
  const inputNodeResponse = await request.post(
    `${backendBase}/api/workbench/workflows/${workflowId}/versions/${draftId}/nodes`,
    {
      data: {
        expected_revision: workflowBody.draft.draft_revision,
        kind: "input",
        label: "恢复检查输入",
        position: { x: 0, y: 180 },
        config: { type: "structured_json", required: true, resolver: "manual" },
      },
    },
  );
  const inputNodeBody = await inputNodeResponse.json();
  expect(inputNodeResponse.status(), JSON.stringify(inputNodeBody)).toBe(201);
  const inputNode = inputNodeBody.node as {
    id: string;
    ports: { outputs: Array<{ id: string }> };
    config: { input_id: string };
  };
  const checkpointNodeLabel = "恢复前固定工具检查";
  const checkpointNodeResponse = await request.post(
    `${backendBase}/api/workbench/workflows/${workflowId}/versions/${draftId}/nodes`,
    {
      data: {
        expected_revision: inputNodeBody.draft.draft_revision,
        kind: "tool",
        label: checkpointNodeLabel,
        position: { x: 320, y: 180 },
        config: {
          tool_id: toolId,
          required_permissions: ["workflow.checkpoint"],
          timeout_sec: 60,
        },
      },
    },
  );
  const checkpointNodeBody = await checkpointNodeResponse.json();
  expect(checkpointNodeResponse.status(), JSON.stringify(checkpointNodeBody)).toBe(201);
  const checkpointNode = checkpointNodeBody.node as {
    id: string;
    ports: {
      inputs: Array<{ id: string }>;
      outputs: Array<{ id: string }>;
    };
  };
  const checkpointNodeId = checkpointNode.id;
  const inputToCheckpointResponse = await request.post(
    `${backendBase}/api/workbench/workflows/${workflowId}/versions/${draftId}/edges`,
    {
      data: {
        expected_revision: checkpointNodeBody.draft.draft_revision,
        source: {
          node_id: inputNode.id,
          port_id: inputNode.ports.outputs[0].id,
        },
        target: {
          node_id: checkpointNode.id,
          port_id: checkpointNode.ports.inputs[0].id,
        },
      },
    },
  );
  const inputToCheckpointBody = await inputToCheckpointResponse.json();
  expect(
    inputToCheckpointResponse.status(),
    JSON.stringify(inputToCheckpointBody),
  ).toBe(201);
  const approvalNodeResponse = await request.post(
    `${backendBase}/api/workbench/workflows/${workflowId}/versions/${draftId}/nodes`,
    {
      data: {
        expected_revision: inputToCheckpointBody.draft.draft_revision,
        kind: "human_approval",
        label: "操作员审批",
        position: { x: 640, y: 180 },
        config: { approval_timeout_sec: 3600 },
      },
    },
  );
  const approvalNodeBody = await approvalNodeResponse.json();
  expect(approvalNodeResponse.status(), JSON.stringify(approvalNodeBody)).toBe(201);
  const approvalNode = approvalNodeBody.node as {
    id: string;
    ports: { inputs: Array<{ id: string }> };
  };
  const approvalNodeId = approvalNode.id;
  const checkpointToApprovalResponse = await request.post(
    `${backendBase}/api/workbench/workflows/${workflowId}/versions/${draftId}/edges`,
    {
      data: {
        expected_revision: approvalNodeBody.draft.draft_revision,
        source: {
          node_id: checkpointNode.id,
          port_id: checkpointNode.ports.outputs[0].id,
        },
        target: {
          node_id: approvalNode.id,
          port_id: approvalNode.ports.inputs[0].id,
        },
      },
    },
  );
  const checkpointToApprovalBody = await checkpointToApprovalResponse.json();
  expect(
    checkpointToApprovalResponse.status(),
    JSON.stringify(checkpointToApprovalBody),
  ).toBe(201);
  const draftRevision = checkpointToApprovalBody.draft.draft_revision as number;

  const publishResponse = await request.post(
    `${backendBase}/api/workbench/workflows/${workflowId}/versions/${draftId}/publish`,
    { data: { expected_revision: draftRevision } },
  );
  expect(publishResponse.ok()).toBeTruthy();
  const versionId = (await publishResponse.json()).version_id as string;

  const taskResponse = await request.post(`${backendBase}/api/workbench/tasks`, {
    data: {
      name: workflowName,
      description: "同一 Attempt 的审批暂停与 checkpoint 恢复",
      workspace_id: workspaceId,
      workflow_id: workflowId,
      workflow_version_id: versionId,
      lifecycle_status: "ready",
      input_values: {
        [inputNode.config.input_id]: { purpose: "durable checkpoint before approval" },
      },
      tags: ["e2e", "phase6", "hitl"],
    },
  });
  expect(taskResponse.ok()).toBeTruthy();
  const taskId = (await taskResponse.json()).task_id as string;

  await page.goto(`/tasks/${taskId}`, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "启动新运行" }).click();
  await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}/runs/task_run_`));
  const taskRunId = page.url().split("/").pop() as string;

  const approvalPanel = page.getByRole("region", { name: "人工审批" });
  await expect(approvalPanel).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("等待人工审批", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Attempt 1", { exact: true })).toBeVisible();
  await expect(approvalPanel).toContainText("操作员审批");
  await expect(approvalPanel).toContainText("durable checkpoint before approval");
  const checkpointNodeButton = page.getByRole("button", {
    name: `查看节点 ${checkpointNodeLabel}`,
  });
  await expect(checkpointNodeButton).toContainText("已完成");
  await expect(page.locator("body")).not.toContainText(approvalNodeId);
  await expect(page.locator("body")).not.toContainText(checkpointNodeId);
  const applicationAlert = page.locator('.ct-v2-run-error[role="alert"]');
  await expect(applicationAlert).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "查看节点 操作员审批" }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "全部事件" }).click();
  const nodeFilter = page.getByLabel("按节点筛选");
  await expect(nodeFilter.locator("option", { hasText: "操作员审批" })).toHaveCount(1);
  await nodeFilter.selectOption({ label: "操作员审批" });
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.getByTitle("复制当前事件").click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).not.toContain(
    approvalNodeId,
  );
  await page.getByRole("tab", { name: "摘要" }).click();
  const decisionRoute = `**/api/workbench/task-runs/${taskRunId}/approvals/${approvalNodeId}/decision`;
  await page.route(decisionRoute, (route) => route.fulfill({
    status: 409,
    contentType: "application/json",
    body: JSON.stringify({ detail: `approval node ${approvalNodeId} already decided` }),
  }));
  await page.getByLabel("审批原因").fill("验证审批失败的安全错误展示");
  await page.getByRole("button", { name: "拒绝" }).click();
  const approvalError = applicationAlert;
  await expect(approvalError).toBeVisible();
  await expect(approvalError).not.toContainText(approvalNodeId);
  await page.unroute(decisionRoute);
  await page.getByLabel("关闭错误").click();
  await expect(applicationAlert).toHaveCount(0);
  await page.screenshot({
    path: path.join(evidenceRoot, "hitl-waiting-desktop.png"),
    fullPage: false,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("region", { name: "人工审批" })).toBeVisible();
  await expect(approvalPanel.getByRole("button", { name: "批准" })).toBeInViewport();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
  ).toBeLessThanOrEqual(1);
  await page.screenshot({
    path: path.join(evidenceRoot, "hitl-waiting-mobile.png"),
    fullPage: false,
  });
  await page.setViewportSize({ width: 1440, height: 900 });

  const dataDir = process.env.CODETALK_PLAYWRIGHT_DATA_DIR;
  expect(dataDir).toBeTruthy();
  const attemptRoot = path.join(
    dataDir as string,
    "workbench",
    "task_runs",
    taskRunId,
  );
  const checkpointPath = path.join(
    attemptRoot,
    "checkpoints",
    `${checkpointNodeId}.json`,
  );
  await expect.poll(() => checkpointEvidence(checkpointPath, {
    taskId,
    taskRunId,
    nodeId: checkpointNodeId,
  }) !== null, { timeout: 30_000 }).toBe(true);
  const beforeRestartCheckpoint = checkpointEvidence(checkpointPath, {
    taskId,
    taskRunId,
    nodeId: checkpointNodeId,
  });
  expect(beforeRestartCheckpoint).not.toBeNull();

  await expect.poll(async () => {
    const events = await taskRunEvents(request, taskRunId);
    const checkpointIndex = events.findIndex(
      (event) =>
        event.event_type === "node_checkpoint_committed" &&
        event.payload?.node_id === checkpointNodeId,
    );
    const approvalWaitIndex = events.findIndex(
      (event) =>
        event.event_type === "node_waiting" && event.payload?.node_id === approvalNodeId,
    );
    return checkpointIndex >= 0 && approvalWaitIndex > checkpointIndex;
  }, { timeout: 30_000 }).toBe(true);

  const preRestartEvents = await taskRunEvents(request, taskRunId);
  expect(
    preRestartEvents.filter(
      (event) => event.event_type === "node_reused" && event.payload?.node_id === checkpointNodeId,
    ),
  ).toHaveLength(0);
  const preRestartMaxEventId = Math.max(0, ...preRestartEvents.map((event) => event.event_id));

  const runUrl = page.url();
  await page.goto("about:blank");
  await restartBackendProcessWhenRequested();
  await page.goto(runUrl, { waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}/runs/${taskRunId}$`));
  await expect(page.getByRole("region", { name: "人工审批" })).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByText("Attempt 1", { exact: true })).toBeVisible();
  await expect(checkpointNodeButton).toContainText("已完成");
  await expect(approvalPanel).toContainText("已从检查点恢复");
  await expect(page.locator("body")).not.toContainText(checkpointNodeId);
  await page.screenshot({
    path: path.join(evidenceRoot, "hitl-recovered-desktop.png"),
    fullPage: false,
  });

  const decisionResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith(
        `/api/workbench/task-runs/${taskRunId}/approvals/${approvalNodeId}/decision`,
      ),
  );
  await page.getByLabel("审批原因").fill("Phase 6 real browser recovery approved");
  await page.getByRole("button", { name: "批准" }).click();
  expect((await decisionResponse).status()).toBe(202);

  await expect.poll(async () => {
    const response = await request.get(
      `${backendBase}/api/workbench/task-runs/${taskRunId}`,
    );
    const payload = await response.json();
    return payload.execution_status ?? payload.status;
  }, { timeout: 30_000 }).toBe("completed");

  await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}/runs/${taskRunId}$`));
  await expect(page.getByText("Attempt 1", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "人工审批" })).toHaveCount(0);
  await expect(
    page.locator(".ct-v2-run-status").filter({ hasText: "执行状态" }).getByText("已完成", {
      exact: true,
    }),
  ).toBeVisible({ timeout: 30_000 });
  await expect(
    page.getByRole("button", { name: /查看节点/ }).first().getByText("已完成", {
      exact: true,
    }),
  ).toBeVisible({ timeout: 30_000 });
  await page.screenshot({
    path: path.join(evidenceRoot, "hitl-resumed-completed-desktop.png"),
    fullPage: false,
  });

  const eventsResponse = await request.get(
    `${backendBase}/api/workbench/task-runs/${taskRunId}/events?limit=1000`,
  );
  expect(eventsResponse.ok()).toBeTruthy();
  const events = (await eventsResponse.json()).items as TaskRunEvent[];
  const eventTypes = events.map((event) => event.event_type);
  expect(eventTypes).toContain("node_waiting");
  expect(eventTypes).toContain("human_approval_decided");
  expect(eventTypes).toContain("node_checkpoint_committed");
  expect(eventTypes).toContain("completed");
  expect(
    events.filter(
      (event) =>
        event.event_type === "node_checkpoint_committed" &&
        event.payload?.node_id === checkpointNodeId,
    ),
  ).toHaveLength(1);
  const reuseEvents = events.filter(
      (event) => event.event_type === "node_reused" && event.payload?.node_id === checkpointNodeId,
    );
  expect(reuseEvents).toHaveLength(1);
  expect(reuseEvents[0].event_id).toBeGreaterThan(preRestartMaxEventId);

  expect(fs.existsSync(path.join(attemptRoot, "approvals", `${approvalNodeId}.json`))).toBe(
    true,
  );
  expect(fs.existsSync(path.join(attemptRoot, "checkpoints", `${approvalNodeId}.json`))).toBe(
    true,
  );
  expect(checkpointEvidence(checkpointPath, {
    taskId,
    taskRunId,
    nodeId: checkpointNodeId,
  })).toEqual(beforeRestartCheckpoint);
});

type TaskRunEvent = {
  event_id: number;
  event_type: string;
  payload?: Record<string, unknown>;
};

type CheckpointEvidence = {
  contentHash: string;
  mtimeNs: bigint;
  payload: Record<string, unknown>;
};

async function taskRunEvents(
  request: import("@playwright/test").APIRequestContext,
  taskRunId: string,
): Promise<TaskRunEvent[]> {
  const response = await request.get(
    `${backendBase}/api/workbench/task-runs/${taskRunId}/events?limit=1000`,
  );
  if (!response.ok()) return [];
  const payload = await response.json();
  return Array.isArray(payload.items) ? payload.items as TaskRunEvent[] : [];
}

function checkpointEvidence(
  checkpointPath: string,
  identity: { taskId: string; taskRunId: string; nodeId: string },
): CheckpointEvidence | null {
  try {
    const content = fs.readFileSync(checkpointPath);
    const payload = JSON.parse(content.toString("utf8")) as Record<string, unknown>;
    if (
      payload.task_id !== identity.taskId ||
      payload.attempt_id !== identity.taskRunId ||
      payload.node_id !== identity.nodeId ||
      payload.status !== "completed" ||
      typeof payload.idempotency_key !== "string" ||
      typeof payload.input_hash !== "string" ||
      !Number.isInteger(payload.revision)
    ) {
      return null;
    }
    return {
      contentHash: crypto.createHash("sha256").update(content).digest("hex"),
      mtimeNs: fs.statSync(checkpointPath, { bigint: true }).mtimeNs,
      payload,
    };
  } catch {
    return null;
  }
}

function assertPhase6RestartRuntime() {
  expect(frontendPort).toBe("3233");
  expect(backendPort).toBe("3234");
  expect(process.env.CODETALK_E2E_BACKEND_PID_FILE).toBeTruthy();
  expect(process.env.CODETALK_WORKFLOW_MANAGED_TOOL_MANIFEST_DIR).toBeTruthy();
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
      input_schema: {
        type: "object",
        additionalProperties: true,
      },
      required_permissions: ["workflow.checkpoint"],
    }),
    "utf8",
  );
}

async function restartBackendProcessWhenRequested() {
  const pidFile = process.env.CODETALK_E2E_BACKEND_PID_FILE;
  if (!pidFile) {
    throw new Error("CODETALK_E2E_BACKEND_PID_FILE is required for Phase 6 restart coverage.");
  }
  const originalPid = Number(fs.readFileSync(pidFile, "utf8").trim());
  expect(Number.isSafeInteger(originalPid) && originalPid > 1).toBe(true);
  process.kill(originalPid, "SIGTERM");

  await expect.poll(async () => {
    let currentPid = originalPid;
    try {
      currentPid = Number(fs.readFileSync(pidFile, "utf8").trim());
    } catch {
      return false;
    }
    if (!Number.isSafeInteger(currentPid) || currentPid <= 1 || currentPid === originalPid) {
      return false;
    }
    try {
      const response = await fetch(`${backendBase}/health`, { cache: "no-store" });
      return response.ok;
    } catch {
      return false;
    }
  }, { timeout: 60_000 }).toBe(true);
}
