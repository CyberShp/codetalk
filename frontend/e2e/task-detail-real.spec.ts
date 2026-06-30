import { expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

test("prevents duplicate task cancellation requests from a real double click", async ({
  page,
  request,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-task-cancel-")));
  fs.writeFileSync(path.join(repo, "README.md"), "task detail cancel e2e\n", "utf8");

  const createResp = await request.post(`${backendBase}/api/tasks`, {
    data: {
      name: `task-cancel-${Date.now()}`,
      repo_path: repo,
      tools: [],
      analysis_focus: "Task cancellation",
      prompt_content: "Create a pending task so the UI cancellation path can be exercised.",
    },
  });
  expect(createResp.status()).toBe(201);
  const task = (await createResp.json()) as { id: string; name: string };

  await page.goto(`/tasks/${task.id}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: task.name })).toBeVisible({
    timeout: 15_000,
  });

  const cancelRequests: string[] = [];
  page.on("request", (req) => {
    if (
      req.method() === "POST" &&
      new URL(req.url()).pathname === `/api/tasks/${task.id}/cancel`
    ) {
      cancelRequests.push(req.url());
    }
  });
  const firstCancel = page.waitForRequest(
    (req) =>
      req.method() === "POST" &&
      new URL(req.url()).pathname === `/api/tasks/${task.id}/cancel`,
  );

  await page.getByRole("button", { name: "取消任务" }).hover();
  await page.getByRole("button", { name: "取消任务" }).dblclick();
  await firstCancel;

  await expect(page.getByText("已取消")).toBeVisible({ timeout: 15_000 });
  await expect.poll(() => cancelRequests.length).toBe(1);
});

test("task detail refresh reloads backend status through the real UI", async ({
  page,
  request,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk_task_refresh_")));
  fs.writeFileSync(path.join(repo, "README.md"), "task detail refresh e2e\n", "utf8");

  const createResp = await request.post(`${backendBase}/api/tasks`, {
    data: {
      name: `task_refresh_${Date.now()}`,
      repo_path: repo,
      tools: [],
      analysis_focus: "Task refresh",
      prompt_content: "Create a pending task so refresh can reload backend status.",
    },
  });
  expect(createResp.status()).toBe(201);
  const task = (await createResp.json()) as { id: string; name: string };

  await page.goto(`/tasks/${task.id}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: task.name })).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByText("等待中")).toBeVisible();

  const cancelResp = await request.post(`${backendBase}/api/tasks/${task.id}/cancel`);
  expect(cancelResp.ok()).toBeTruthy();
  await expect(page.getByText("等待中")).toBeVisible();

  const refreshedTask = page.waitForResponse(
    (resp) =>
      resp.request().method() === "GET" &&
      new URL(resp.url()).pathname === `/api/tasks/${task.id}` &&
      resp.ok(),
  );
  await page.getByTitle("刷新").hover();
  await page.getByTitle("刷新").click();
  await refreshedTask;

  await expect(page.getByText("已取消")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("button", { name: "取消任务" })).toHaveCount(0);
});
