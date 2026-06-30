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
