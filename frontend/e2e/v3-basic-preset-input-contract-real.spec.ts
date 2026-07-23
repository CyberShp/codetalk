import { expect, test } from "@playwright/test";

import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "V3 basic preset input contract real E2E",
});

test("basic source preset collects a separately named multiline analysis target through the UI", async ({ page }) => {
  test.setTimeout(90_000);
  const stamp = Date.now();
  const workspaceName = `V3 输入契约 SPDK ${stamp}`;

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
  await page.getByLabel("工作空间名称").fill(workspaceName);
  await page.getByLabel("代码仓库路径").fill("/Volumes/Media/dpdk/spdk");
  await page.getByRole("button", { name: "创建工作空间" }).click();
  await expect(page).toHaveURL(/\/workspaces\//, { timeout: 30_000 });

  await page.goto("/tasks/new", { waitUntil: "domcontentloaded" });
  await page.getByRole("radio", { name: /基础源码报告（Codex CLI）/ }).check();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByLabel("任务名称 *").fill(`V3 基础 A ${stamp}`);
  await page.getByLabel("工作空间 *").selectOption({ label: workspaceName });
  await page.getByRole("button", { name: "保存并继续" }).click();

  await expect(page.getByRole("heading", { name: "填写本次输入" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "分析目标 *" })).toBeVisible();
  await expect(page.getByText(/用户逐字要求，定义分析范围/)).toBeVisible();
});
