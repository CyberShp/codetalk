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

test("opens a persisted AI review thread from a prepared workbench run through the real UI", async ({
  page,
  request,
}) => {
  test.setTimeout(60_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-review-run-")));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "nvmf", "connect.c"),
    "int nvmf_connect_review_target(void) { return 0; }\n",
    "utf8",
  );
  const workspaceId = `ai-review-ws-${Date.now()}`;

  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
  await page.getByLabel("Workspace ID").fill(workspaceId);
  await page.getByRole("button", { name: "工作流设计" }).hover();
  await page.getByRole("button", { name: "工作流设计" }).click();
  await page.getByLabel("工作流预设").selectOption("module_analysis");
  await page.getByRole("button", { name: "安装预设" }).hover();
  await page.getByRole("button", { name: "安装预设" }).click();
  await expect(page.getByText("预设已安装: 模块分析工作流")).toBeVisible({
    timeout: 15_000,
  });

  await page.getByRole("button", { name: "运行驾驶舱" }).hover();
  await page.getByRole("button", { name: "运行驾驶舱" }).click();
  await page.getByLabel("Repo path").fill(repo);
  await page.getByLabel("Workflow input repo_path").fill(repo);
  await page.getByLabel("Workflow input analysis_object").fill("lib/nvmf connect review");
  await page.getByRole("button", { name: "准备运行" }).hover();
  await page.getByRole("button", { name: "准备运行" }).click();
  await expect(page.getByText(/Task run prepared:/)).toBeVisible({ timeout: 15_000 });
  const preparedText = await page.locator("body").innerText();
  const taskRunId = preparedText.match(/Task run prepared:\s*(task_run_[a-f0-9]+)/)?.[1] ?? "";
  expect(taskRunId).not.toEqual("");
  await expect(page.getByText(repo).first()).toBeVisible();

  const conversationPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().includes("/api/ai/conversations") &&
      response.status() === 201,
  );
  await page.getByRole("button", { name: "围绕本次运行继续追问" }).hover();
  await page.getByRole("button", { name: "围绕本次运行继续追问" }).click();
  const conversationResponse = await conversationPromise;
  const conversationFromCreate = (await conversationResponse.json()) as {
    id: string;
    title: string;
    scope_type: string;
    scope_id: string;
    workspace_id: string;
    memory_namespace: string;
    initial_context: Record<string, unknown>;
  };

  await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "模块分析工作流 · AI 复盘" })).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByText(`workbench_task_run / ${taskRunId}`)).toBeVisible();
  await expect(page.getByPlaceholder(/像 Codex 一样继续追问/)).toBeVisible();
  await expect(page.locator("code").filter({ hasText: `workspace:${workspaceId}` })).toBeVisible();

  const conversationResp = await request.get(
    `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}/api/ai/conversations/${conversationFromCreate.id}`,
  );
  expect(conversationResp.ok()).toBeTruthy();
  const persisted = (await conversationResp.json()) as {
    title: string;
    scope_type: string;
    scope_id: string;
    workspace_id: string;
    memory_namespace: string;
    initial_context: Record<string, unknown>;
  };
  expect(persisted.title).toBe("模块分析工作流 · AI 复盘");
  expect(persisted.scope_type).toBe("workbench_task_run");
  expect(persisted.scope_id).toBe(taskRunId);
  expect(persisted.workspace_id).toBe(workspaceId);
  expect(persisted.memory_namespace).toBe(`workspace:${workspaceId}`);
  expect(persisted.initial_context).toMatchObject({
    workflow_id: "module_analysis",
    workspace_id: workspaceId,
    memory_namespace: `workspace:${workspaceId}`,
    repo_path: repo,
  });
});

test("persists semantic cases and evidence source slices through the real workbench UI", async ({
  page,
}) => {
  const unique = Date.now();
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-knowledge-")));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "nvmf", "tcp.c"),
    [
      "int nvmf_tcp_connect(void) {",
      "    return 0;",
      "}",
      "int nvmf_tcp_disconnect(void) {",
      "    return -1;",
      "}",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceId = `knowledge-ws-${unique}`;
  const semanticScenario = `NVMe TCP reconnect drops stale qp ${unique}`;
  const fileScenario = `NVMe TCP exported semantic case ${unique}`;
  const caseId = `tc_nvmf_tcp_reconnect_${unique}`;
  const fileCaseId = `tc_nvmf_tcp_file_import_${unique}`;
  const evidenceSubject = `nvmf_tcp_connect_${unique}`;
  const evidenceText = `Manual evidence for ${evidenceSubject} covers reconnect public behavior`;

  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
  await page.getByLabel("Workspace ID").fill(workspaceId);
  await page.getByLabel("Repo path").fill(repo);
  await page.getByRole("button", { name: "证据与语义" }).hover();
  await page.getByRole("button", { name: "证据与语义" }).click();

  await expect(page.getByRole("heading", { name: "测试语义库" })).toBeVisible();
  await page.getByLabel("Semantic feature").fill("NVMe TCP reconnect");
  await page.getByLabel("Semantic module").fill("nvmf_tcp");
  await page.getByLabel("Semantic case lines").fill(semanticScenario);
  await page.getByRole("button", { name: "生成语义 JSON" }).hover();
  await page.getByRole("button", { name: "生成语义 JSON" }).click();
  await expect(page.getByText("语义导入草稿已生成: 1 cases")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByLabel("Semantic JSON")).toHaveValue(new RegExp(semanticScenario));

  await page.getByLabel("Semantic JSON").fill(
    JSON.stringify(
      {
        case_id: caseId,
        feature: "NVMe TCP reconnect",
        module: "nvmf_tcp",
        test_level: "black_box",
        scenario: semanticScenario,
        terms: ["reconnect", "stale qp"],
        tags: ["recovery", "spdk"],
        preconditions: "NVMe-oF target is reachable over TCP.",
        steps: ["Disconnect the initiator connection.", "Reconnect through the public CLI."],
        expected: "The public connection state recovers without stale queue pairs.",
        assertion_style: "black_box_observable",
      },
      null,
      2,
    ),
  );
  await page.getByRole("button", { name: "导入用例" }).hover();
  await page.getByRole("button", { name: "导入用例" }).click();
  await expect(page.getByText(`语义用例已保存: ${caseId}`)).toBeVisible({
    timeout: 15_000,
  });

  await page.getByLabel("Semantic case file").setInputFiles({
    name: "semantic-cases.jsonl",
    mimeType: "application/jsonl",
    buffer: Buffer.from(
      `${JSON.stringify({
        case_id: fileCaseId,
        scenario: fileScenario,
        terms: ["exported semantic", "black-box"],
        tags: ["file-import"],
      })}\n`,
    ),
  });
  await expect(page.getByText("semantic-cases.jsonl")).toBeVisible();
  await page.getByRole("button", { name: "导入文件" }).hover();
  await page.getByRole("button", { name: "导入文件" }).click();
  await expect(page.getByText("语义文件已导入: 1, rejected: 0")).toBeVisible({
    timeout: 15_000,
  });

  await page.getByLabel("Semantic search query").fill(String(unique));
  await page.getByRole("button", { name: "搜索", exact: true }).hover();
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page.getByText("语义搜索结果: 2")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator("p").filter({ hasText: caseId })).toBeVisible();
  await expect(page.locator("p").filter({ hasText: semanticScenario })).toBeVisible();
  await expect(page.locator("p").filter({ hasText: fileCaseId })).toBeVisible();
  await expect(page.locator("p").filter({ hasText: fileScenario })).toBeVisible();

  await page.getByLabel("Evidence subject").fill(evidenceSubject);
  await page.getByLabel("Evidence path").fill("lib/nvmf/tcp.c");
  await page.getByLabel("Evidence text").fill(evidenceText);
  await page.getByRole("button", { name: "保存证据" }).hover();
  await page.getByRole("button", { name: "保存证据" }).click();
  await expect(page.getByText(/证据已保存: .*source slices 1/)).toBeVisible({
    timeout: 15_000,
  });

  await page.getByRole("button", { name: "搜索证据" }).hover();
  await page.getByRole("button", { name: "搜索证据" }).click();
  await expect(page.getByText("证据搜索结果: 1")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator("span").filter({ hasText: evidenceSubject })).toBeVisible();
  await expect(page.getByText("lib/nvmf/tcp.c").first()).toBeVisible();
  await expect(page.getByText("usable:true")).toBeVisible();

  await page.getByRole("button", { name: "源码切片" }).hover();
  await page.getByRole("button", { name: "源码切片" }).click();
  await expect(page.getByText("源码切片已加载: 1")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("1 slice(s)")).toBeVisible();
  await expect(page.getByText(/lib\/nvmf\/tcp\.c:1-/)).toBeVisible();
  await expect(page.getByText("verified_current")).toBeVisible();
  await expect(page.getByText("int nvmf_tcp_connect(void) {")).toBeVisible();
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
  await expect(page.getByText(/black_box_cases:accepted artifact:black_box_cases\.json/).first()).toBeVisible();

  await expect(page.getByRole("button", { name: "固化输出" })).toBeEnabled({
    timeout: 15_000,
  });
  await page.getByRole("button", { name: "固化输出" }).hover();
  await page.getByRole("button", { name: "固化输出" }).click();
  await expect(page.getByText(/Workflow outputs materialized:/)).toBeVisible({
    timeout: 15_000,
  });

  const materializationArtifact = page
    .getByRole("button")
    .filter({ hasText: /workflow_output_materialization\.json/ })
    .first();
  await expect(materializationArtifact).toBeVisible({ timeout: 15_000 });
  await materializationArtifact.hover();
  await materializationArtifact.click();
  await expect(page.getByText("workflow_output_materialization.json").first()).toBeVisible();
  await expect(page.getByText(/Materialized evidence:/)).toBeVisible();
  await expect(page.getByText(/Declared outputs:/)).toBeVisible();
  await expect(page.getByText(/black_box_cases:accepted artifact:black_box_cases\.json/).first()).toBeVisible();
  await expect(page.getByText(/workflow_outputs sha:/)).toBeVisible();

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
