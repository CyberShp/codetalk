import { expect, test } from "@playwright/test";

import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "V3 managed Agent intranet readiness real E2E",
});

test("Codex readiness reports an actionable intranet block before any workflow is started", async ({ page }) => {
  test.setTimeout(45_000);

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/settings", { waitUntil: "domcontentloaded" });
  const runtime = page.getByTestId("agent-runtime-card-default-codex");
  await expect(runtime).toBeVisible({ timeout: 30_000 });
  await runtime.getByRole("button", { name: "测试" }).hover();
  await runtime.getByRole("button", { name: "测试" }).click();
  await expect(runtime).toContainText(
    /不可用：内网策略未批准 Agent 访问模型端点/,
    { timeout: 30_000 },
  );
  await expect(runtime).toContainText(/使用内置模型的已批准 Provider Adapter/);
});
