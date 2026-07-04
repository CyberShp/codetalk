import { expect, test, type APIRequestContext } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;
const frontendPort = process.env.CODETALK_FRONTEND_PORT ?? "3003";
const backendPort = process.env.CODETALK_BACKEND_PORT ?? "3004";

assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "AI thread bounded layout real E2E",
  frontendPort,
  backendPort,
});

type WorkspaceRecord = { id: string; name: string };
type ConversationRecord = { id: string; title: string };

async function createWorkspace(request: APIRequestContext, runId: string, index: number): Promise<WorkspaceRecord> {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), `codetalk-ai-layout-${runId}-${index}-`)));
  fs.writeFileSync(path.join(repo, "README.md"), `AI bounded layout real E2E ${runId} ${index}\n`, "utf8");
  const name = `ai-layout-e2e-${runId}-${String(index).padStart(2, "0")}`;
  const response = await request.post(`${backendBase}/api/workspaces`, {
    data: { name, repo_path: repo },
  });
  expect(response.status()).toBe(201);
  const workspace = (await response.json()) as WorkspaceRecord;
  return { id: workspace.id, name };
}

async function createConversation(
  request: APIRequestContext,
  workspace: WorkspaceRecord,
  runId: string,
  index: number,
): Promise<ConversationRecord> {
  const title = `Layout thread ${runId} ${String(index).padStart(2, "0")}`;
  const response = await request.post(`${backendBase}/api/ai/conversations`, {
    data: {
      scope_type: "workspace",
      scope_id: workspace.id,
      workspace_id: workspace.id,
      memory_namespace: `workspace:${workspace.id}`,
      runtime_type: "builtin_llm",
      title,
      initial_context: {
        workspace_id: workspace.id,
        project_name: workspace.name,
        memory_namespace: `workspace:${workspace.id}`,
      },
    },
  });
  expect(response.status()).toBe(201);
  const conversation = (await response.json()) as ConversationRecord;
  return { id: conversation.id, title };
}

test("AI thread keeps crowded project and thread rails inside bounded scroll areas on mobile", async ({
  page,
  request,
}) => {
  const runId = `${Date.now()}`;
  const workspaces: WorkspaceRecord[] = [];
  const conversations: ConversationRecord[] = [];
  try {
    for (let index = 0; index < 14; index += 1) {
      workspaces.push(await createWorkspace(request, runId, index));
    }
    for (let index = 0; index < 32; index += 1) {
      conversations.push(await createConversation(request, workspaces[0], runId, index));
    }

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/ai", { waitUntil: "domcontentloaded" });
    const primaryProject = page.locator("button").filter({ hasText: workspaces[0].name }).first();
    await expect(primaryProject).toBeVisible({ timeout: 15_000 });
    await primaryProject.hover();
    await primaryProject.click();
    await expect(page.getByRole("heading", { name: workspaces[0].name })).toBeVisible();
    await expect(page.getByText(conversations[0].title)).toBeVisible();

    const hubMetrics = await page.evaluate(() => ({
      bodyScrollHeight: document.documentElement.scrollHeight,
      viewportHeight: window.innerHeight,
    }));
    expect(hubMetrics.bodyScrollHeight).toBeLessThanOrEqual(hubMetrics.viewportHeight + 12);

    const projectListMetrics = await page.locator(".ct-ai-home__project-list").evaluate((node) => {
      const element = node as HTMLElement;
      return {
        clientHeight: element.clientHeight,
        scrollHeight: element.scrollHeight,
        overflowY: window.getComputedStyle(element).overflowY,
      };
    });
    expect(projectListMetrics.overflowY).toBe("auto");
    expect(projectListMetrics.scrollHeight).toBeGreaterThan(projectListMetrics.clientHeight);

    const hubThreadListMetrics = await page.locator(".ct-thread-timeline").evaluate((node) => {
      const element = node as HTMLElement;
      return {
        clientHeight: element.clientHeight,
        scrollHeight: element.scrollHeight,
        overflowY: window.getComputedStyle(element).overflowY,
      };
    });
    expect(hubThreadListMetrics.overflowY).toBe("auto");
    expect(hubThreadListMetrics.scrollHeight).toBeGreaterThan(hubThreadListMetrics.clientHeight);

    await page.goto(`/ai/${conversations[0].id}`, { waitUntil: "domcontentloaded" });

    await expect(page.getByRole("heading", { name: conversations[0].title })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("搜索 AI 项目")).toBeVisible();
    await expect(page.getByLabel("搜索 AI 线程")).toBeVisible();
    await expect(page.getByText(/已收起 \d+ 个项目/)).toBeVisible();
    await expect(page.getByText(/已收起 \d+ 条线程/)).toBeVisible();

    const pageMetrics = await page.evaluate(() => ({
      bodyScrollHeight: document.documentElement.scrollHeight,
      viewportHeight: window.innerHeight,
      bodyOverflowY: window.getComputedStyle(document.documentElement).overflowY,
    }));
    expect(pageMetrics.bodyScrollHeight).toBeLessThanOrEqual(pageMetrics.viewportHeight + 12);

    const railMetrics = await page.locator(".ct-codex-ai__rail").evaluate((node) => {
      const element = node as HTMLElement;
      const rect = element.getBoundingClientRect();
      return {
        bottom: rect.bottom,
        height: rect.height,
        viewportHeight: window.innerHeight,
      };
    });
    expect(railMetrics.height).toBeLessThanOrEqual(railMetrics.viewportHeight - 12);
    expect(railMetrics.bottom).toBeLessThanOrEqual(railMetrics.viewportHeight + 1);

    const threadListMetrics = await page.locator(".ct-codex-ai__thread-list").evaluate((node) => {
      const element = node as HTMLElement;
      return {
        clientHeight: element.clientHeight,
        scrollHeight: element.scrollHeight,
        overflowY: window.getComputedStyle(element).overflowY,
      };
    });
    expect(threadListMetrics.overflowY).toBe("auto");
    expect(threadListMetrics.scrollHeight).toBeGreaterThan(threadListMetrics.clientHeight);
  } finally {
    for (const conversation of conversations) {
      await request.delete(`${backendBase}/api/ai/conversations/${encodeURIComponent(conversation.id)}`).catch(() => undefined);
    }
  }
});

test("AI thread keeps crowded thread rail bounded on desktop", async ({ page, request }) => {
  const runId = `${Date.now()}`;
  const workspace = await createWorkspace(request, runId, 100);
  const conversations: ConversationRecord[] = [];
  try {
    for (let index = 0; index < 56; index += 1) {
      conversations.push(await createConversation(request, workspace, runId, index));
    }

    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/ai/${conversations[0].id}`, { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: conversations[0].title })).toBeVisible({
      timeout: 15_000,
    });
    await page.getByLabel("搜索 AI 线程").hover();

    const pageMetrics = await page.evaluate(() => ({
      bodyScrollHeight: document.documentElement.scrollHeight,
      viewportHeight: window.innerHeight,
      bodyOverflowY: window.getComputedStyle(document.documentElement).overflowY,
    }));
    expect(pageMetrics.bodyScrollHeight).toBeLessThanOrEqual(pageMetrics.viewportHeight + 12);

    const railMetrics = await page.locator(".ct-codex-ai__rail").evaluate((node) => {
      const element = node as HTMLElement;
      const rect = element.getBoundingClientRect();
      return {
        bottom: rect.bottom,
        height: rect.height,
        viewportHeight: window.innerHeight,
        overflowY: window.getComputedStyle(element).overflowY,
      };
    });
    expect(railMetrics.overflowY).toBe("hidden");
    expect(railMetrics.height).toBeLessThanOrEqual(railMetrics.viewportHeight - 12);
    expect(railMetrics.bottom).toBeLessThanOrEqual(railMetrics.viewportHeight + 1);

    const threadListMetrics = await page.locator(".ct-codex-ai__thread-list").evaluate((node) => {
      const element = node as HTMLElement;
      const rect = element.getBoundingClientRect();
      return {
        bottom: rect.bottom,
        clientHeight: element.clientHeight,
        scrollHeight: element.scrollHeight,
        overflowY: window.getComputedStyle(element).overflowY,
        viewportHeight: window.innerHeight,
      };
    });
    expect(threadListMetrics.overflowY).toBe("auto");
    expect(threadListMetrics.scrollHeight).toBeGreaterThan(threadListMetrics.clientHeight);
    expect(threadListMetrics.bottom).toBeLessThanOrEqual(threadListMetrics.viewportHeight + 1);
  } finally {
    for (const conversation of conversations) {
      await request.delete(`${backendBase}/api/ai/conversations/${encodeURIComponent(conversation.id)}`).catch(() => undefined);
    }
  }
});

test("AI thread deletes the active idle thread from the rail and opens a fallback thread", async ({
  page,
  request,
}) => {
  const runId = `${Date.now()}`;
  const workspace = await createWorkspace(request, runId, 200);
  const fallbackThread = await createConversation(request, workspace, runId, 200);
  const deletedThread = await createConversation(request, workspace, runId, 201);

  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/ai/${deletedThread.id}`, { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: deletedThread.title })).toBeVisible({
      timeout: 15_000,
    });

    page.once("dialog", async (dialog) => {
      expect(dialog.message()).toContain(deletedThread.title);
      await dialog.accept();
    });

    const deletedRow = page.locator(".ct-codex-ai__thread-row").filter({ hasText: deletedThread.title });
    await expect(deletedRow).toBeVisible();
    await deletedRow.hover();
    await deletedRow.getByRole("button", { name: `删除线程 ${deletedThread.title}` }).click();

    await expect(page).toHaveURL(new RegExp(`/ai/${fallbackThread.id}$`));
    await expect(page.getByRole("heading", { name: fallbackThread.title })).toBeVisible();
    await expect(page.locator(".ct-codex-ai__thread-row").filter({ hasText: deletedThread.title })).toHaveCount(0);

    const deletedResponse = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(deletedThread.id)}`,
    );
    expect(deletedResponse.status()).toBe(404);
  } finally {
    await request.delete(`${backendBase}/api/ai/conversations/${encodeURIComponent(fallbackThread.id)}`).catch(() => undefined);
    await request.delete(`${backendBase}/api/ai/conversations/${encodeURIComponent(deletedThread.id)}`).catch(() => undefined);
  }
});
