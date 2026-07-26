import { expect, test } from "@playwright/test";
import path from "node:path";

const taskId = process.env.CODETALK_E2E_QUALITY_RETRY_TASK_ID ?? "";
const parentRunId = process.env.CODETALK_E2E_QUALITY_RETRY_PARENT_RUN_ID ?? "";
const runId = process.env.CODETALK_E2E_QUALITY_RETRY_RUN_ID ?? "";
const evidenceRoot = process.env.CODETALK_PLAYWRIGHT_DATA_DIR ?? "";

test.skip(!taskId || (!parentRunId && !runId), "Set a persisted retry parent/run pair to inspect the real retry UI.");

test("quality retry preserves verified stage support artifacts", async ({ page }) => {
  test.setTimeout(8 * 60_000);
  const initialRunId = runId || parentRunId;
  await page.goto(`/tasks/${encodeURIComponent(taskId)}/runs/${encodeURIComponent(initialRunId)}`, {
    waitUntil: "domcontentloaded",
  });

  if (!runId) {
    const retry = page.getByRole("button", { name: "修复质量问题并重试" });
    await expect(retry).toBeVisible();
    await retry.click();
    await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}/runs/task_run_`));
    await expect(page).not.toHaveURL(new RegExp(`/runs/${parentRunId}$`));
  }

  await expect(page.locator("main h1")).toBeVisible();
  await expect(page.locator(".ct-v2-run-status").filter({ hasText: "执行状态" })).toContainText(/运行中|已完成/);
  await expect(page.getByText("执行完成，质量待修复").or(page.getByText("质量检查中")).or(page.getByText("质量通过"))).toBeVisible({
    timeout: 7 * 60_000,
  });
  await expect(page.getByText("测试活动阶段契约不完整")).toHaveCount(0);
  if (evidenceRoot) {
    await page.screenshot({
      path: path.join(evidenceRoot, "v3-quality-retry-support-preserved.png"),
      fullPage: false,
    });
  }
});
