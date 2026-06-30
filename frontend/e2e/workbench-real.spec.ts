import { expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

test("installs a workflow preset and validates required inputs through the real workbench UI", async ({
  page,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-workbench-")));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.writeFileSync(path.join(repo, "lib", "nvmf", "README.md"), "NVMe-oF target notes\n", "utf8");

  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "工作流设计" }).hover();
  await page.getByRole("button", { name: "工作流设计" }).click();

  await page.getByLabel("工作流预设").selectOption("module_analysis");
  await page.getByRole("button", { name: "安装预设" }).hover();
  await page.getByRole("button", { name: "安装预设" }).click();
  await expect(page.getByText(/预设已安装: 模块分析工作流/)).toBeVisible({ timeout: 15_000 });

  await page.getByRole("button", { name: "运行驾驶舱" }).hover();
  await page.getByRole("button", { name: "运行驾驶舱" }).click();
  await expect(page.getByRole("heading", { name: "任务运行" })).toBeVisible();
  await page.getByLabel("Repo path").fill(repo);
  await page.getByLabel("Workflow input repo_path").fill(repo);
  await expect(page.getByLabel("Workflow input analysis_object")).toBeVisible();
  await expect(page.getByRole("button", { name: "准备运行" })).toBeEnabled();
  await page.getByRole("button", { name: "准备运行" }).hover();
  await page.getByRole("button", { name: "准备运行" }).click();

  await expect(page.getByText("required input analysis_object is missing")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByText(/Task run prepared:/)).toHaveCount(0);

  await page.getByLabel("Workflow input analysis_object").fill("lib/nvmf");
  await page.getByRole("button", { name: "准备运行" }).hover();
  await page.getByRole("button", { name: "准备运行" }).click();

  await expect(page.getByText(/Task run prepared:/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/Agent runs:/)).toBeVisible();
  await expect(page.getByText(repo)).toBeVisible();

  await page.getByRole("button", { name: "审计产物" }).hover();
  await page.getByRole("button", { name: "审计产物" }).click();
  await expect(page.getByText(/产物已加载:/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/审计产物: \d+/)).toBeVisible();

  const taskBundleArtifact = page.getByRole("button", {
    name: /task_bundle:task_bundle\.json/,
  });
  await expect(taskBundleArtifact).toBeVisible();
  await taskBundleArtifact.hover();
  await taskBundleArtifact.click();
  await expect(page.getByText("task_bundle.json").first()).toBeVisible();
  await expect(page.getByText("module_analysis").first()).toBeVisible();
  await expect(page.getByText("lib/nvmf").first()).toBeVisible();

  await page.getByRole("button", { name: "复跑计划" }).hover();
  await page.getByRole("button", { name: "复跑计划" }).click();
  await expect(page.getByText(/Rerun plan .*:/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/Rerun: .* \/ steps \d+/)).toBeVisible();
  await expect(page.getByText(/validation:/)).toBeVisible();
  await expect(page.getByText(/can-rerun:/)).toBeVisible();

  await page.getByRole("button", { name: "验收审计" }).hover();
  await page.getByRole("button", { name: "验收审计" }).click();
  await expect(page.getByText(/Acceptance audit .*:/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/Acceptance:/)).toBeVisible();
  await expect(page.getByText(/missing-required:/)).toBeVisible();

  await expect(page.getByText(repo).first()).toBeVisible();
});
