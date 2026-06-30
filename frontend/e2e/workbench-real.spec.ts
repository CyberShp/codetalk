import { expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

test("lists and installs every required workflow preset through the real workbench UI", async ({
  page,
}) => {
  const presets = [
    { id: "module_analysis", label: "模块分析工作流" },
    { id: "resource_leak_hunt", label: "资源/异常路径排查工作流" },
    { id: "mr_blackbox_test", label: "MR 黑盒测试工作流" },
    { id: "patch_impact_review", label: "补丁影响面评审工作流" },
  ];

  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "工作流设计" }).hover();
  await page.getByRole("button", { name: "工作流设计" }).click();

  const presetSelect = page.getByLabel("工作流预设");
  await expect(presetSelect).toBeVisible({ timeout: 15_000 });

  for (const preset of presets) {
    await expect(
      page.locator(`select[aria-label="工作流预设"] option[value="${preset.id}"]`),
    ).toHaveCount(1);
    await presetSelect.selectOption(preset.id);
    await page.getByRole("button", { name: "安装预设" }).hover();
    await page.getByRole("button", { name: "安装预设" }).click();
    await expect(page.getByText(`预设已安装: ${preset.label}`)).toBeVisible({
      timeout: 15_000,
    });
  }
});

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

test("executes resource leak hunt and previews materialized artifacts through the real workbench UI", async ({
  page,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-risk-hunt-")));
  fs.mkdirSync(path.join(repo, "lib", "bdev"), { recursive: true });
  fs.mkdirSync(path.join(repo, "test", "bdev"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "bdev", "cleanup.c"),
    [
      "#include <stdlib.h>",
      "void *bdev_create(void) {",
      "    void *buf = malloc(128);",
      "    if (!buf) { return NULL; }",
      "    if (spdk_bdev_open_ext(\"Malloc0\", true, NULL, NULL, NULL) != 0) { goto err; }",
      "    free(buf);",
      "    return buf;",
      "err:",
      "    return NULL;",
      "}",
      "",
    ].join("\n"),
    "utf8",
  );

  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "工作流设计" }).hover();
  await page.getByRole("button", { name: "工作流设计" }).click();
  await page.getByLabel("工作流预设").selectOption("resource_leak_hunt");
  await page.getByRole("button", { name: "安装预设" }).hover();
  await page.getByRole("button", { name: "安装预设" }).click();
  await expect(page.getByText("预设已安装: 资源/异常路径排查工作流")).toBeVisible({
    timeout: 15_000,
  });

  await page.getByRole("button", { name: "运行驾驶舱" }).hover();
  await page.getByRole("button", { name: "运行驾驶舱" }).click();
  await page.getByLabel("Repo path").fill(repo);
  await page.getByLabel("Inputs JSON").fill(
    JSON.stringify(
      {
        target_scope: "lib/bdev cleanup",
        risk_pattern: "cleanup",
        repo_path: repo,
      },
      null,
      2,
    ),
  );

  await page.getByRole("button", { name: "准备运行" }).hover();
  await page.getByRole("button", { name: "准备运行" }).click();
  await expect(page.getByText(/Task run prepared:/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("button", { name: "执行工作流" })).toBeEnabled({
    timeout: 15_000,
  });
  await page.getByRole("button", { name: "执行工作流" }).hover();
  await page.getByRole("button", { name: "执行工作流" }).click();
  await expect(page.getByText(/Workflow execution completed:/)).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByText(/工作流: completed/)).toBeVisible();

  const riskArtifact = page
    .getByRole("button")
    .filter({ hasText: /risk_findings\.json/ })
    .first();
  await expect(riskArtifact).toBeVisible({ timeout: 15_000 });
  await riskArtifact.hover();
  await riskArtifact.click();
  await expect(page.getByText("risk_findings.json").first()).toBeVisible();
  await expect(page.getByText("local-resource-scan").first()).toBeVisible();
  await expect(page.getByText("lib/bdev/cleanup.c").first()).toBeVisible();
  await expect(page.getByText(/failure_mode/).first()).toBeVisible();
  await expect(page.getByText(/test\/bdev/).first()).toBeVisible();

  const testHooksArtifact = page
    .getByRole("button")
    .filter({ hasText: /test_hooks\.json/ })
    .first();
  await expect(testHooksArtifact).toBeVisible();
});
