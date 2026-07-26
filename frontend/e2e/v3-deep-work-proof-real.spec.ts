import { expect, test } from "@playwright/test";
import path from "node:path";

const taskId = process.env.CODETALK_E2E_DEEP_PROOF_TASK_ID ?? "";
const runId = process.env.CODETALK_E2E_DEEP_PROOF_RUN_ID ?? "";
const evidenceRoot = process.env.CODETALK_PLAYWRIGHT_DATA_DIR ?? "";

test.skip(!taskId || !runId, "Set the persisted real deep task/run IDs to inspect its cockpit proof.");

test("deep cockpit exposes bounded evidence of real provider work", async ({ page }) => {
  await page.goto(`/tasks/${encodeURIComponent(taskId)}/runs/${encodeURIComponent(runId)}`, {
    waitUntil: "domcontentloaded",
  });

  const proof = page.getByRole("region", { name: "深度执行工作量证明" });
  await expect(proof).toBeVisible();
  await expect(proof).toContainText("深度执行证明");
  await expect(proof).toContainText("已验证");
  await expect(proof).toContainText(/模型调用/);
  await expect(proof).toContainText(/输出 token/);
  await expect(proof).toContainText(/定向探索分支/);
  await proof.scrollIntoViewIfNeeded();
  if (evidenceRoot) {
    await page.screenshot({
      path: path.join(evidenceRoot, "v3-deep-work-proof-cockpit.png"),
      fullPage: false,
    });
  }
});
