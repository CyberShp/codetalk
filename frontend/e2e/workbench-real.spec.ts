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

test("executes patch impact review and previews flow impact artifacts through the real workbench UI", async ({
  page,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-patch-impact-")));
  fs.mkdirSync(path.join(repo, "lib", "bdev"), { recursive: true });
  fs.mkdirSync(path.join(repo, "test", "bdev"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "bdev", "bdev.c"),
    "int spdk_bdev_submit_request(void) { return 0; }\n",
    "utf8",
  );
  const patchDiff = [
    "diff --git a/lib/bdev/bdev.c b/lib/bdev/bdev.c",
    "index 0000000..1111111 100644",
    "--- a/lib/bdev/bdev.c",
    "+++ b/lib/bdev/bdev.c",
    "@@ -1,1 +1,1 @@",
    "-int spdk_bdev_submit_request(void) { return 0; }",
    "+int spdk_bdev_submit_request(void) { return -22; }",
  ].join("\n");

  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "工作流设计" }).hover();
  await page.getByRole("button", { name: "工作流设计" }).click();
  await page.getByLabel("工作流预设").selectOption("patch_impact_review");
  await page.getByRole("button", { name: "安装预设" }).hover();
  await page.getByRole("button", { name: "安装预设" }).click();
  await expect(page.getByText("预设已安装: 补丁影响面评审工作流")).toBeVisible({
    timeout: 15_000,
  });

  await page.getByRole("button", { name: "运行驾驶舱" }).hover();
  await page.getByRole("button", { name: "运行驾驶舱" }).click();
  await page.getByLabel("Repo path").fill(repo);
  await page.getByLabel("Inputs JSON").fill(
    JSON.stringify(
      {
        patch_diff: patchDiff,
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

  const impactArtifact = page
    .getByRole("button")
    .filter({ hasText: /impact_scope\.json/ })
    .first();
  await expect(impactArtifact).toBeVisible({ timeout: 15_000 });
  await impactArtifact.hover();
  await impactArtifact.click();
  await expect(page.getByText("impact_scope.json").first()).toBeVisible();
  await expect(page.getByText("local-patch-impact").first()).toBeVisible();
  await expect(page.getByText("lib/bdev/bdev.c").first()).toBeVisible();
  await expect(page.getByText("spdk_bdev_submit_request").first()).toBeVisible();
  await expect(page.getByText(/test\/bdev/).first()).toBeVisible();

  const flowDeltaArtifact = page
    .getByRole("button")
    .filter({ hasText: /flow_delta\.json/ })
    .first();
  await expect(flowDeltaArtifact).toBeVisible();
  const testRecommendationsArtifact = page
    .getByRole("button")
    .filter({ hasText: /test_recommendations\.json/ })
    .first();
  await expect(testRecommendationsArtifact).toBeVisible();
});

test("executes MR black-box workflow and previews public test cases through the real workbench UI", async ({
  page,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-mr-blackbox-")));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.mkdirSync(path.join(repo, "test", "nvmf"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "nvmf", "ctrlr.c"),
    "int nvmf_ctrlr_connect(void) { return 0; }\n",
    "utf8",
  );
  const patchDiff = [
    "diff --git a/lib/nvmf/ctrlr.c b/lib/nvmf/ctrlr.c",
    "index 0000000..1111111 100644",
    "--- a/lib/nvmf/ctrlr.c",
    "+++ b/lib/nvmf/ctrlr.c",
    "@@ -1,1 +1,1 @@",
    "-int nvmf_ctrlr_connect(void) { return 0; }",
    "+int nvmf_ctrlr_connect(void) { return -1; }",
  ].join("\n");

  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "工作流设计" }).hover();
  await page.getByRole("button", { name: "工作流设计" }).click();
  await page.getByLabel("工作流预设").selectOption("mr_blackbox_test");
  await page.getByRole("button", { name: "安装预设" }).hover();
  await page.getByRole("button", { name: "安装预设" }).click();
  await expect(page.getByText("预设已安装: MR 黑盒测试工作流")).toBeVisible({
    timeout: 15_000,
  });

  await page.getByRole("button", { name: "运行驾驶舱" }).hover();
  await page.getByRole("button", { name: "运行驾驶舱" }).click();
  await page.getByLabel("Repo path").fill(repo);
  await page.getByLabel("Inputs JSON").fill(
    JSON.stringify(
      {
        patch_diff: patchDiff,
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

  const blackBoxCasesArtifact = page
    .getByRole("button")
    .filter({ hasText: /black_box_cases\.json/ })
    .first();
  await expect(blackBoxCasesArtifact).toBeVisible({ timeout: 15_000 });
  await blackBoxCasesArtifact.hover();
  await blackBoxCasesArtifact.click();
  await expect(page.getByText("black_box_cases.json").first()).toBeVisible();
  await expect(page.getByText("local-mr-blackbox").first()).toBeVisible();
  await expect(page.getByText("black_box_ready").first()).toBeVisible();
  await expect(page.getByText("lib/nvmf/ctrlr.c").first()).toBeVisible();
  await expect(page.getByText("test/nvmf").first()).toBeVisible();
  await expect(page.getByText("observable_signals").first()).toBeVisible();
  await expect(page.getByText("no direct internal function invocation").first()).toBeVisible();
  await expect(page.getByText(/call internal functions/i)).toHaveCount(0);

  await expect(page.getByText(/mr_scope:accepted artifact:mr_snapshot\.json/)).toBeVisible();
  await expect(page.getByText(/black_box_cases:accepted artifact:black_box_cases\.json/)).toBeVisible();

  await page.reload({ waitUntil: "domcontentloaded" });
  const recentRun = page.getByRole("button", { name: /mr_blackbox_test/ }).first();
  await expect(recentRun).toBeVisible({ timeout: 15_000 });
  await recentRun.hover();
  await recentRun.click();
  await expect(page.getByText(/Task run restored: task_run_/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/工作流: completed/)).toBeVisible();
  await expect(page.getByText(/Acceptance:\s*ready/)).toBeVisible();
  await expect(page.getByText(/mr_scope:accepted artifact:mr_snapshot\.json/)).toBeVisible();

  const restoredBlackBoxCasesArtifact = page
    .getByRole("button")
    .filter({ hasText: /black_box_cases\.json/ })
    .first();
  await expect(restoredBlackBoxCasesArtifact).toBeVisible();
  await restoredBlackBoxCasesArtifact.hover();
  await restoredBlackBoxCasesArtifact.click();
  await expect(page.getByText("black_box_cases.json").first()).toBeVisible();
  await expect(page.getByText("local-mr-blackbox").first()).toBeVisible();
});
