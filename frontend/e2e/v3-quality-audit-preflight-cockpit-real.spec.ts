import { expect, test } from "@playwright/test";

import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "V3 independent quality-audit preflight cockpit E2E",
});

test("a Codex workflow blocks missing independent quality audit before analysis starts", async ({ page }) => {
  test.setTimeout(90_000);
  const stamp = Date.now();
  const workspaceName = `V3 质量门禁 SPDK ${stamp}`;

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
  await page.getByLabel("工作空间名称").fill(workspaceName);
  await page.getByLabel("代码仓库路径").fill("/Volumes/Media/dpdk/spdk");
  await page.getByRole("button", { name: "创建工作空间" }).hover();
  await page.getByRole("button", { name: "创建工作空间" }).click();
  await expect(page).toHaveURL(/\/workspaces\//, { timeout: 30_000 });

  await page.goto("/tasks/new", { waitUntil: "domcontentloaded" });
  await page.getByRole("radio", { name: /基础源码报告（Codex CLI）/ }).check();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByLabel("任务名称 *").fill(`V3 Codex 质量门禁 ${stamp}`);
  const workspaceSelector = page.getByLabel("工作空间 *");
  await expect(workspaceSelector.locator("option")).toHaveCount(2);
  await workspaceSelector.selectOption({ index: 1 });
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByRole("textbox", { name: "分析目标 *" }).fill(
    "分析 SPDK iSCSI login 流程及其异常、资源、并发与恢复测试设计。",
  );
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.getByRole("heading", { name: "检查并运行" })).toBeVisible();

  const startedAt = Date.now();
  await page.getByRole("button", { name: "保存并运行" }).hover();
  await page.getByRole("button", { name: "保存并运行" }).click();
  await expect(page).toHaveURL(/\/tasks\/task_[^/]+\/runs\/task_run_/, { timeout: 30_000 });

  const failurePanel = page.locator(".ct-v2-run-failure");
  await expect(failurePanel.getByRole("heading", { name: "独立质量核验未就绪" })).toBeVisible({
    timeout: 30_000,
  });
  await expect(failurePanel).toContainText("未配置可用的活跃聊天模型");
  await expect(failurePanel).toContainText("请在设置中选择活跃聊天模型");
  await expect(failurePanel.getByRole("link", { name: "检查执行器设置" })).toBeVisible();
  expect(Date.now() - startedAt).toBeLessThan(60_000);
});
