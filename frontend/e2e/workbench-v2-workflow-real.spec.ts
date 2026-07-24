import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "Workbench V2 workflow authoring real E2E",
});

test("creates, edits, compiles, trial-runs, and publishes a workflow through the UI", async ({ page }) => {
  test.setTimeout(120_000);
  const stamp = Date.now();
  const workflowName = `Workbench V2 E2E ${stamp}`;
  const workflowId = `workbench-v2-e2e-${stamp}`;
  const workspaceName = `Workbench V2 Repo ${stamp}`;
  const e2eArtifactRoot = "/Volumes/Media/codetalk-e2e-artifacts/v3-node-trial-repositories";
  fs.mkdirSync(e2eArtifactRoot, { recursive: true });
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(e2eArtifactRoot, "codetalk-workbench-v2-")));
  fs.writeFileSync(path.join(repo, "README.md"), "# Real Workbench V2 source\n", "utf8");
  execFileSync("git", ["init", "-q", repo]);

  await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
  await page.getByPlaceholder(/项目 A/).fill(workspaceName);
  await page.getByPlaceholder(/本地文件夹路径/).fill(repo);
  const createWorkspace = page.getByRole("button", { name: "创建工作空间" });
  await createWorkspace.hover();
  await createWorkspace.click();
  await page.waitForURL(/\/workspaces\/[0-9a-f-]{36}$/);

  await page.goto("/workflows/new", { waitUntil: "domcontentloaded" });
  await page.getByLabel(/工作流名称/).pressSequentially(workflowName);
  const workflowIdInput = page.getByLabel("工作流 ID");
  await workflowIdInput.focus();
  await page.keyboard.press(process.platform === "darwin" ? "Meta+a" : "Control+a");
  await workflowIdInput.pressSequentially(workflowId);
  await expect(workflowIdInput).toHaveValue(workflowId);
  await page.getByLabel("描述").pressSequentially("通过真实浏览器验证输入、Agent、输出、编译计划和试运行契约。");

  const continueButton = page.getByRole("button", { name: /保存并继续/ });
  const continueWithKeyboard = async (currentStep: number) => {
    if (currentStep > 1) await expect(page).toHaveURL(new RegExp(`[?&]step=${currentStep}(?:&|$)`));
    await expect(continueButton).toBeEnabled();
    await continueButton.focus();
    expect(await continueButton.evaluate((button) => document.activeElement === button)).toBe(true);
    await page.keyboard.press("Enter");
  };
  await continueWithKeyboard(1);
  await expect(page.getByText("定义输入", { exact: true }).first()).toBeVisible();
  await continueWithKeyboard(2);
  await expect(page.getByText("定义执行节点", { exact: true }).first()).toBeVisible();
  await expect(page.getByLabel("执行器").locator("option").filter({ hasText: "builtin-llm" })).toHaveCount(1);
  await continueWithKeyboard(3);
  await expect(page.getByText("定义输出", { exact: true }).first()).toBeVisible();
  await continueWithKeyboard(4);
  await expect(page.getByRole("region", { name: "工作流画布" })).toBeVisible();

  const agentNode = page.getByRole("article", { name: /源码分析 Agent节点/ });
  await expect(agentNode).toBeVisible();
  const before = await agentNode.boundingBox();
  expect(before).not.toBeNull();
  await page.mouse.move(before!.x + 70, before!.y + 24);
  await page.mouse.down();
  await page.mouse.move(before!.x + 140, before!.y + 84, { steps: 8 });
  await page.mouse.up();
  const after = await agentNode.boundingBox();
  expect(after!.x).toBeGreaterThan(before!.x + 30);

  await continueWithKeyboard(5);
  await expect(page.getByText("验证结果", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "验证" }).click();
  await expect(page.getByText("验证通过", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "编译计划" }).click();
  await expect(page.getByText("analyze", { exact: true })).toBeVisible();

  await page.getByLabel("工作空间").selectOption({ label: workspaceName });
  await page.getByLabel("分析对象 *").pressSequentially("README.md 中定义的真实工作流源码分析流程");
  await page.getByRole("button", { name: "启动试运行" }).hover();
  await page.getByRole("button", { name: "启动试运行" }).click();
  await expect(page.getByText("运行已启动", { exact: false })).toBeVisible({ timeout: 30_000 });

  await page.getByRole("button", { name: "发布工作流" }).click();
  await page.waitForURL(new RegExp(`/workflows/${workflowId}/versions$`));
  await expect(page.getByText("V1", { exact: true })).toBeVisible();
  await expect(page.getByText("已发布", { exact: true })).toBeVisible();
});
