import path from "node:path";

import { expect, test } from "@playwright/test";

import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "V3 basic design-document input contract real E2E",
});

test("basic source and design preset keeps target and uploaded design document as separate UI inputs", async ({ page }) => {
  test.setTimeout(90_000);
  const stamp = Date.now();
  const workspaceName = `V3 设计输入 SPDK ${stamp}`;
  const designDocument = path.join(
    process.cwd(),
    "e2e/fixtures/v3-iscsi-login-design.md",
  );

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
  await page.getByLabel("工作空间名称").fill(workspaceName);
  await page.getByLabel("代码仓库路径").fill("/Volumes/Media/dpdk/spdk");
  await page.getByRole("button", { name: "创建工作空间" }).click();
  await expect(page).toHaveURL(/\/workspaces\//, { timeout: 30_000 });

  await page.goto("/tasks/new", { waitUntil: "domcontentloaded" });
  await page.getByRole("radio", { name: /基础源码 \+ 设计文档报告（内置模型）/ }).check();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByLabel("任务名称 *").fill(`V3 基础 B ${stamp}`);
  const workspaceSelector = page.getByLabel("工作空间 *");
  await expect(workspaceSelector.locator("option")).toHaveCount(2);
  await workspaceSelector.selectOption({ index: 1 });
  await page.getByRole("button", { name: "保存并继续" }).click();

  await expect(page.getByRole("heading", { name: "填写本次输入" })).toBeVisible();
  await page.getByRole("textbox", { name: "分析目标 *" }).fill(
    "分析 SPDK iSCSI login 的流程、异常、资源、并发与恢复测试设计。",
  );
  await expect(page.getByText(/用户逐字要求，定义分析范围/)).toBeVisible();
  await expect(page.getByText(/开发设计文档.*本地文件/)).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles(designDocument);
  await expect(page.getByText("已选择 1 个文件")).toBeVisible();

  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.getByRole("heading", { name: "确认执行配置" })).toBeVisible();
  await page.getByRole("button", { name: "上一步" }).click();
  await expect(page.getByRole("textbox", { name: "分析目标 *" })).toHaveValue(
    "分析 SPDK iSCSI login 的流程、异常、资源、并发与恢复测试设计。",
  );
  await expect(page.getByText("已选择 1 个文件")).toBeVisible();
});
