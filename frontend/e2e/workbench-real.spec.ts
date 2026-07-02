import { expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

test("lists and installs every built-in workflow preset through the real workbench UI", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const presets = [
    { id: "module_analysis", label: "模块分析工作流" },
    { id: "resource_leak_hunt", label: "资源/异常路径排查工作流" },
    { id: "source_flow_sfmea_blackbox", label: "代码分析-流程-SFMEA-黑盒用例工作流" },
    { id: "nvmf_connect_io_blackbox", label: "NVMe-oF 连接/IO 黑盒场景" },
    { id: "iscsi_login_session_blackbox", label: "iSCSI 登录/会话黑盒场景" },
    { id: "bdev_io_reset_blackbox", label: "bdev IO/reset 黑盒场景" },
    { id: "rpc_config_negative_blackbox", label: "RPC/config 负例黑盒场景" },
    { id: "reactor_thread_poller_blackbox", label: "reactor/thread/poller 调度黑盒场景" },
    { id: "nvmf_disconnect_reconnect_blackbox", label: "NVMe-oF 断连/重连黑盒场景" },
    { id: "iscsi_auth_failure_blackbox", label: "iSCSI 认证失败/重置黑盒场景" },
    { id: "bdev_failover_resource_blackbox", label: "bdev failover/资源压力黑盒场景" },
    { id: "blobstore_ftl_recovery_blackbox", label: "blobstore/FTL 恢复黑盒场景" },
    { id: "vhost_vfio_user_lifecycle_blackbox", label: "vhost/vfio-user 生命周期黑盒场景" },
    { id: "nvmf_tcp_tls_auth_blackbox", label: "NVMe/TCP TLS/认证黑盒场景" },
    { id: "bdev_qos_latency_blackbox", label: "bdev QoS/时延退化黑盒场景" },
    { id: "jsonrpc_concurrency_idempotency_blackbox", label: "JSON-RPC 并发/幂等黑盒场景" },
    { id: "app_startup_shutdown_smoke_blackbox", label: "应用启动/关闭冒烟黑盒场景" },
    { id: "nvme_ctrlr_hotplug_reset_blackbox", label: "NVMe 控制器热插拔/reset 黑盒场景" },
    { id: "storage_capacity_enospc_recovery_blackbox", label: "容量/ENOSPC 恢复黑盒场景" },
    { id: "nvmf_rdma_transport_blackbox", label: "NVMe/RDMA transport 黑盒场景" },
    { id: "iscsi_digest_multi_connection_blackbox", label: "iSCSI digest/多连接黑盒场景" },
    { id: "bdev_hotremove_io_error_blackbox", label: "bdev hotremove/IO 错误黑盒场景" },
    { id: "blobstore_metadata_powerfail_blackbox", label: "blobstore 元数据/掉电恢复黑盒场景" },
    { id: "rpc_security_authz_blackbox", label: "RPC 安全/权限黑盒场景" },
    { id: "fault_injection_timeout_recovery_blackbox", label: "故障注入/超时恢复黑盒场景" },
    { id: "concurrent_operations_stress_blackbox", label: "并发操作/压力黑盒场景" },
    { id: "observability_diagnostics_blackbox", label: "可观测性/诊断黑盒场景" },
    { id: "config_compatibility_rollback_blackbox", label: "配置兼容/回滚黑盒场景" },
    { id: "lvol_snapshot_clone_blackbox", label: "lvol 快照/克隆黑盒场景" },
    { id: "raid_degraded_rebuild_blackbox", label: "RAID 降级/rebuild 黑盒场景" },
    { id: "nvme_multipath_failover_blackbox", label: "NVMe multipath/failover 黑盒场景" },
    { id: "env_hugepage_memory_blackbox", label: "环境/hugepage 内存黑盒场景" },
    { id: "spdk_cli_rpc_smoke_blackbox", label: "SPDK CLI/RPC 冒烟黑盒场景" },
    { id: "basic_lifecycle_smoke_blackbox", label: "基础生命周期冒烟黑盒场景" },
    { id: "io_stress_performance_blackbox", label: "IO 压力/性能基线黑盒场景" },
    { id: "failure_recovery_soak_blackbox", label: "故障恢复/soak 黑盒场景" },
    { id: "transport_network_partition_blackbox", label: "transport 网络分区黑盒场景" },
    { id: "data_integrity_corruption_blackbox", label: "数据完整性/损坏黑盒场景" },
    { id: "upgrade_compatibility_persistence_blackbox", label: "升级兼容/持久化黑盒场景" },
    { id: "telemetry_metrics_regression_blackbox", label: "遥测/指标回归黑盒场景" },
    { id: "mr_blackbox_test", label: "MR 黑盒测试工作流" },
    { id: "patch_impact_review", label: "补丁影响面评审工作流" },
  ];

  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "工作流设计" }).hover();
  await page.getByRole("button", { name: "工作流设计" }).click();

  const presetSelect = page.getByLabel("工作流预设");
  await expect(presetSelect).toBeVisible({ timeout: 15_000 });
  await expect(presetSelect.locator("option")).toHaveCount(presets.length);
  await expect(page.getByLabel("Workflow JSON")).not.toHaveValue(
    /"id": "mr-blackbox-workflow"/,
  );
  const scenarioValues = await page
    .getByLabel("Workflow builder scenario")
    .locator("option")
    .evaluateAll((options) => options.map((option) => option.getAttribute("value")));
  expect(scenarioValues).toEqual(
    expect.arrayContaining([
      "module_analysis",
      "resource_leak_hunt",
      "mr_blackbox_test",
      "patch_impact_review",
      "nvmf_rdma_transport_blackbox",
      "iscsi_digest_multi_connection_blackbox",
      "bdev_hotremove_io_error_blackbox",
      "blobstore_metadata_powerfail_blackbox",
      "rpc_security_authz_blackbox",
      "fault_injection_timeout_recovery_blackbox",
      "concurrent_operations_stress_blackbox",
      "observability_diagnostics_blackbox",
      "config_compatibility_rollback_blackbox",
      "lvol_snapshot_clone_blackbox",
      "raid_degraded_rebuild_blackbox",
      "nvme_multipath_failover_blackbox",
      "env_hugepage_memory_blackbox",
      "spdk_cli_rpc_smoke_blackbox",
      "basic_lifecycle_smoke_blackbox",
      "io_stress_performance_blackbox",
      "failure_recovery_soak_blackbox",
      "transport_network_partition_blackbox",
      "data_integrity_corruption_blackbox",
      "upgrade_compatibility_persistence_blackbox",
      "telemetry_metrics_regression_blackbox",
    ]),
  );

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
    await expect(page.getByLabel("Workflow JSON")).toHaveValue(
      new RegExp(`"id": "${preset.id}"`),
    );
  }
});

test("prevents duplicate workflow preset install requests from a real double click", async ({
  page,
}) => {
  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "工作流设计" }).hover();
  await page.getByRole("button", { name: "工作流设计" }).click();

  const presetSelect = page.getByLabel("工作流预设");
  await expect(presetSelect).toBeVisible({ timeout: 15_000 });
  await presetSelect.selectOption("module_analysis");

  const installRequests: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      request.url().includes("/api/workbench/workflow-presets/module_analysis/install")
    ) {
      installRequests.push(request.url());
    }
  });
  const installRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().includes("/api/workbench/workflow-presets/module_analysis/install"),
  );

  await page.getByRole("button", { name: "安装预设" }).hover();
  await page.getByRole("button", { name: "安装预设" }).dblclick();
  await installRequest;
  await expect(page.getByRole("button", { name: "安装预设" })).toBeDisabled();
  await expect(page.getByText("预设已安装: 模块分析工作流")).toBeVisible({
    timeout: 15_000,
  });
  await expect.poll(() => installRequests.length).toBe(1);
});

test("prevents duplicate workflow saves from a real double click", async ({
  page,
}) => {
  const unique = Date.now();
  const workflowId = `double_save_${unique}`;

  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "工作流设计" }).hover();
  await page.getByRole("button", { name: "工作流设计" }).click();
  await page.getByLabel("Workflow JSON").fill(
    JSON.stringify(
      {
        id: workflowId,
        name: "Double Save E2E",
        version: 1,
        inputs: [{ id: "analysis_object", type: "free_text", required: true }],
        steps: [
          {
            id: "inspect",
            type: "agent_task",
            provider: "local-search",
            required_artifacts: ["double_save.json"],
            goal: "Inspect duplicate save guard.",
          },
        ],
        outputs: [{ id: "result", type: "json", artifact: "double_save.json" }],
      },
      null,
      2,
    ),
  );

  const saveRequests: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/workbench/workflows"
    ) {
      saveRequests.push(request.url());
    }
  });
  const saveRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/workbench/workflows",
  );

  await page.getByRole("button", { name: "保存工作流" }).hover();
  await page.getByRole("button", { name: "保存工作流" }).dblclick();
  await saveRequest;
  await expect(page.getByRole("button", { name: "保存工作流" })).toBeDisabled();
  await expect(page.getByText(`工作流已保存: ${workflowId}`)).toBeVisible({
    timeout: 15_000,
  });
  await expect.poll(() => saveRequests.length).toBe(1);
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

test("locks conflicting task run actions while a real prepare request is in flight", async ({
  page,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-busy-run-")));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "nvmf", "connect.c"),
    "int nvmf_busy_connect(void) { return 0; }\n",
    "utf8",
  );

  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
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
  await page.getByLabel("Workflow input analysis_object").fill("lib/nvmf busy connect");

  const prepareRequests: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      request.url().includes("/api/workbench/task-runs/prepare")
    ) {
      prepareRequests.push(request.url());
    }
  });
  const prepareRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().includes("/api/workbench/task-runs/prepare"),
  );
  await page.getByRole("button", { name: "准备运行" }).hover();
  await page.getByRole("button", { name: "准备运行" }).dblclick();
  await prepareRequest;

  await expect(page.getByRole("button", { name: "创建并运行" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "准备运行" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "执行工作流" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "审计产物" })).toBeDisabled();

  await expect(page.getByText(/Task run prepared:/)).toBeVisible({ timeout: 15_000 });
  await expect.poll(() => prepareRequests.length).toBe(1);
});

test("prevents duplicate create-and-run task runs from a real double click", async ({
  page,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-create-run-")));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "nvmf", "create_run.c"),
    "int nvmf_create_run_probe(void) { return 0; }\n",
    "utf8",
  );

  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
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
  await page.getByLabel("Workflow input analysis_object").fill("lib/nvmf create run");

  const runRequests: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/workbench/task-runs/run"
    ) {
      runRequests.push(request.url());
    }
  });
  const runRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/workbench/task-runs/run",
  );

  await page.getByRole("button", { name: "创建并运行" }).hover();
  await page.getByRole("button", { name: "创建并运行" }).dblclick();
  await runRequest;

  await expect(page.getByRole("button", { name: "创建并运行" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "准备运行" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "执行工作流" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "审计产物" })).toBeDisabled();

  await expect(page.getByText(/Task run completed:/)).toBeVisible({ timeout: 30_000 });
  await expect.poll(() => runRequests.length).toBe(1);
});

test("creates, runs, previews, and downloads workflow artifacts through the real workbench UI", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-run-artifacts-")));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "nvmf", "artifact_flow.c"),
    [
      "int nvmf_artifact_flow_connect(void) {",
      "    return 0;",
      "}",
      "",
    ].join("\n"),
    "utf8",
  );

  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
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
  await page.getByLabel("Workflow input analysis_object").fill("lib/nvmf artifact flow");
  await page.getByRole("button", { name: "创建并运行" }).hover();
  await page.getByRole("button", { name: "创建并运行" }).click();

  await expect(page.getByText(/Task run completed:/)).toBeVisible({ timeout: 45_000 });
  const bodyText = await page.locator("body").innerText();
  const taskRunId = bodyText.match(/Task run completed:\s*(task_run_[a-f0-9]+)/)?.[1] ?? "";
  expect(taskRunId).not.toEqual("");
  await expect(page.getByText(/工作流: completed/)).toBeVisible();
  await expect(page.getByText(/审计产物: \d+/)).toBeVisible({ timeout: 15_000 });
  const hiddenArtifactsToggle = page.getByText(/展开其余 \d+ 个产物/);
  await expect(hiddenArtifactsToggle).toBeVisible();
  await hiddenArtifactsToggle.hover();
  await hiddenArtifactsToggle.click();

  const workflowOutputsButton = page.getByRole("button", {
    name: /workflow_outputs:workflow_outputs\.json/,
  });
  await expect(workflowOutputsButton).toBeVisible({ timeout: 15_000 });
  await workflowOutputsButton.hover();
  await workflowOutputsButton.click();
  await expect(page.getByText("workflow_outputs.json").first()).toBeVisible();
  await expect(page.locator("pre").filter({ hasText: "source_scope" }).first()).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.locator("pre").filter({ hasText: "evidence_cards" }).first()).toBeVisible();
  await expect(page.locator("pre").filter({ hasText: "report.md" }).first()).toBeVisible();

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载预览" }).hover();
  await page.getByRole("button", { name: "下载预览" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("workflow_outputs.json");
  const exportPath = testInfo.outputPath("workbench-workflow-outputs.json");
  await download.saveAs(exportPath);
  const exportedText = fs.readFileSync(exportPath, "utf8");
  expect(exportedText).not.toContain(repo);
  const exported = JSON.parse(exportedText) as {
    task_run_id: string;
    status: string;
    outputs: Array<{ id: string; artifact: string; status: string; type: string }>;
  };
  expect(exported.task_run_id).toBe(taskRunId);
  expect(exported.status).toBe("completed");
  expect(exported.outputs).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ id: "scope", artifact: "source_scope.json", status: "ok" }),
      expect.objectContaining({ id: "evidence_cards", artifact: "evidence_cards.json", status: "ok" }),
      expect.objectContaining({ id: "report", artifact: "report.md", status: "ok" }),
    ]),
  );

  const manifestResp = await request.get(
    `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}/api/workbench/task-runs/${encodeURIComponent(taskRunId)}/artifacts`,
  );
  expect(manifestResp.ok()).toBeTruthy();
  const manifest = (await manifestResp.json()) as {
    task_run_id: string;
    artifacts: Array<{ relative_path: string; kind: string; size_bytes: number; sha256: string }>;
  };
  expect(manifest.task_run_id).toBe(taskRunId);
  expect(manifest.artifacts).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        relative_path: "task_artifact_manifest.json",
        kind: "task_artifact_manifest",
      }),
      expect.objectContaining({
        relative_path: "workflow_outputs.json",
        kind: "workflow_outputs",
      }),
      expect.objectContaining({
        relative_path: "task_acceptance_audit.json",
        kind: "task_acceptance_audit",
      }),
    ]),
  );
  for (const artifact of manifest.artifacts) {
    expect(artifact.size_bytes, `${artifact.relative_path} should not be empty`).toBeGreaterThan(0);
    expect(artifact.sha256, `${artifact.relative_path} should have sha256`).toMatch(/^[a-f0-9]{64}$/);
  }
});

test("executes source-flow SFMEA black-box workflow through the real workbench UI", async ({
  page,
}, testInfo) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-source-flow-")));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.mkdirSync(path.join(repo, "test", "nvmf"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "nvmf", "ctrlr.c"),
    [
      "int spdk_nvmf_ctrlr_connect(void) {",
      "    return 0;",
      "}",
      "int spdk_nvmf_ctrlr_submit_io(void) {",
      "    return 0;",
      "}",
      "",
    ].join("\n"),
    "utf8",
  );
  fs.writeFileSync(
    path.join(repo, "test", "nvmf", "nvmf.sh"),
    "# public nvmf connect to IO workflow\n",
    "utf8",
  );

  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "工作流设计" }).hover();
  await page.getByRole("button", { name: "工作流设计" }).click();
  await page.getByLabel("工作流预设").selectOption("source_flow_sfmea_blackbox");
  await page.getByRole("button", { name: "安装预设" }).hover();
  await page.getByRole("button", { name: "安装预设" }).click();
  await expect(page.getByText("预设已安装: 代码分析-流程-SFMEA-黑盒用例工作流")).toBeVisible({
    timeout: 15_000,
  });

  await page.getByRole("button", { name: "运行驾驶舱" }).hover();
  await page.getByRole("button", { name: "运行驾驶舱" }).click();
  await page.getByLabel("Repo path").fill(repo);
  await page.getByLabel("Inputs JSON").fill(
    JSON.stringify(
      {
        analysis_object: "lib/nvmf connect to IO submit flow",
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

  const hiddenArtifactsToggle = page.getByText(/展开其余 \d+ 个产物/);
  if (await hiddenArtifactsToggle.isVisible()) {
    await hiddenArtifactsToggle.hover();
    await hiddenArtifactsToggle.click();
  }

  for (const artifactName of [
    "source_scope.json",
    "evidence_cards.json",
    "flow_map.md",
    "sfmea.json",
    "black_box_cases.json",
  ]) {
    await expect(
      page.getByRole("button").filter({ hasText: new RegExp(artifactName.replace(".", "\\.")) }).first(),
    ).toBeVisible({ timeout: 15_000 });
  }

  const flowArtifact = page.getByRole("button").filter({ hasText: /flow_map\.md/ }).first();
  await flowArtifact.hover();
  await flowArtifact.click();
  await expect(page.getByText("flow_map.md").first()).toBeVisible();
  await expect(page.locator("pre").filter({ hasText: "connect" }).first()).toBeVisible();
  await expect(page.locator("pre").filter({ hasText: "IO" }).first()).toBeVisible();

  const sfmeaArtifact = page.getByRole("button").filter({ hasText: /sfmea\.json/ }).first();
  await sfmeaArtifact.hover();
  await sfmeaArtifact.click();
  await expect(page.getByText("sfmea.json").first()).toBeVisible();
  const sfmeaDownloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载预览" }).hover();
  await page.getByRole("button", { name: "下载预览" }).click();
  const sfmeaDownload = await sfmeaDownloadPromise;
  expect(sfmeaDownload.suggestedFilename()).toMatch(/sfmea\.json$/);
  const sfmeaPath = testInfo.outputPath("source_flow_sfmea.json");
  await sfmeaDownload.saveAs(sfmeaPath);
  const sfmea = JSON.parse(fs.readFileSync(sfmeaPath, "utf8")) as Array<Record<string, unknown>>;
  expect(sfmea.length).toBeGreaterThan(0);
  for (const field of [
    "failure_mode",
    "cause",
    "effect",
    "detection",
    "severity",
    "occurrence",
    "detection_score",
    "rpn",
    "mitigation",
  ]) {
    expect(sfmea[0][field], `SFMEA field ${field}`).toBeTruthy();
  }

  const casesArtifact = page.getByRole("button").filter({ hasText: /black_box_cases\.json/ }).first();
  await casesArtifact.hover();
  await casesArtifact.click();
  await expect(page.getByText("black_box_cases.json").first()).toBeVisible();
  const casesDownloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载预览" }).hover();
  await page.getByRole("button", { name: "下载预览" }).click();
  const casesDownload = await casesDownloadPromise;
  expect(casesDownload.suggestedFilename()).toMatch(/black_box_cases\.json$/);
  const casesPath = testInfo.outputPath("source_flow_black_box_cases.json");
  await casesDownload.saveAs(casesPath);
  const casesText = fs.readFileSync(casesPath, "utf8");
  expect(casesText).toContain("black_box_ready");
  expect(casesText).toContain("public workflow");
  expect(casesText).not.toContain(repo);
  const cases = JSON.parse(casesText) as Array<{ steps?: string[] }>;
  expect(cases.length).toBeGreaterThan(0);
  expect(cases[0].steps ?? []).not.toEqual(
    expect.arrayContaining([expect.stringMatching(/spdk_nvmf_ctrlr_connect|spdk_nvmf_ctrlr_submit_io/)]),
  );

  await expect(page.getByText(/source_scope:accepted artifact:source_scope\.json/)).toBeVisible();
  await expect(page.getByText(/sfmea:accepted artifact:sfmea\.json/)).toBeVisible();
  await expect(page.getByText(/black_box_cases:ok/)).toBeVisible();
  await expect(page.getByText(/Semantic import: ok \/ 1 imported/)).toBeVisible();
  await expect(page.getByText(/source:task_run:task_run_[a-f0-9]+:black_box_cases/)).toBeVisible();
});

test("executes SPDK CLI RPC smoke preset through the real workbench UI", async ({
  page,
}, testInfo) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-spdk-cli-rpc-")));
  fs.mkdirSync(path.join(repo, "scripts"), { recursive: true });
  fs.mkdirSync(path.join(repo, "test", "json_config"), { recursive: true });
  fs.mkdirSync(path.join(repo, "app"), { recursive: true });
  fs.mkdirSync(path.join(repo, "lib", "event"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "scripts", "rpc.py"),
    [
      "def rpc_get_methods(client):",
      "    return client.call('rpc_get_methods')",
      "def bdev_malloc_create(client, size, block_size):",
      "    return client.call('bdev_malloc_create')",
      "def bdev_malloc_delete(client, name):",
      "    return client.call('bdev_malloc_delete')",
      "",
    ].join("\n"),
    "utf8",
  );
  fs.writeFileSync(
    path.join(repo, "app", "spdk_tgt.c"),
    ["int spdk_tgt_start_rpc_ready(void) {", "    return 0;", "}", ""].join("\n"),
    "utf8",
  );
  fs.writeFileSync(
    path.join(repo, "lib", "event", "app.c"),
    ["int spdk_app_rpc_listen_start(void) {", "    return 0;", "}", ""].join("\n"),
    "utf8",
  );
  fs.writeFileSync(
    path.join(repo, "test", "json_config", "rpc_smoke.sh"),
    "# public RPC smoke test: start target, wait for RPC ready, create/list/delete bdev, send invalid command\n",
    "utf8",
  );

  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "工作流设计" }).hover();
  await page.getByRole("button", { name: "工作流设计" }).click();
  await page.getByLabel("工作流预设").selectOption("spdk_cli_rpc_smoke_blackbox");
  await page.getByRole("button", { name: "安装预设" }).hover();
  await page.getByRole("button", { name: "安装预设" }).click();
  await expect(page.getByText("预设已安装: SPDK CLI/RPC 冒烟黑盒场景")).toBeVisible({
    timeout: 15_000,
  });

  await page.getByRole("button", { name: "运行驾驶舱" }).hover();
  await page.getByRole("button", { name: "运行驾驶舱" }).click();
  await page.getByLabel("Repo path").fill(repo);
  await page.getByLabel("Inputs JSON").fill(
    JSON.stringify(
      {
        analysis_object: "SPDK CLI RPC smoke startup readiness create list delete invalid command",
        repo_path: repo,
      },
      null,
      2,
    ),
  );

  await page.getByRole("button", { name: "准备运行" }).hover();
  await page.getByRole("button", { name: "准备运行" }).click();
  await expect(page.getByText(/Task run prepared:/)).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "执行工作流" }).hover();
  await page.getByRole("button", { name: "执行工作流" }).click();
  await expect(page.getByText(/Workflow execution completed:/)).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByText(/工作流: completed/)).toBeVisible();

  const hiddenArtifactsToggle = page.getByText(/展开其余 \d+ 个产物/);
  if (await hiddenArtifactsToggle.isVisible()) {
    await hiddenArtifactsToggle.hover();
    await hiddenArtifactsToggle.click();
  }

  for (const artifactName of [
    "source_scope.json",
    "evidence_cards.json",
    "flow_map.md",
    "sfmea.json",
    "black_box_cases.json",
  ]) {
    await expect(
      page.getByRole("button").filter({ hasText: new RegExp(artifactName.replace(".", "\\.")) }).first(),
    ).toBeVisible({ timeout: 15_000 });
  }

  const scopeArtifact = page.getByRole("button").filter({ hasText: /source_scope\.json/ }).first();
  await scopeArtifact.hover();
  await scopeArtifact.click();
  const scopeDownloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载预览" }).hover();
  await page.getByRole("button", { name: "下载预览" }).click();
  const scopeDownload = await scopeDownloadPromise;
  const scopePath = testInfo.outputPath("spdk_cli_rpc_source_scope.json");
  await scopeDownload.saveAs(scopePath);
  const scopeText = fs.readFileSync(scopePath, "utf8");
  expect(scopeText).toContain("scripts/rpc.py");
  expect(scopeText).toContain("test/json_config/rpc_smoke.sh");

  const flowArtifact = page.getByRole("button").filter({ hasText: /flow_map\.md/ }).first();
  await flowArtifact.hover();
  await flowArtifact.click();
  await expect(page.locator("pre").filter({ hasText: /RPC|smoke|ready/i }).first()).toBeVisible();

  const casesArtifact = page.getByRole("button").filter({ hasText: /black_box_cases\.json/ }).first();
  await casesArtifact.hover();
  await casesArtifact.click();
  const casesDownloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载预览" }).hover();
  await page.getByRole("button", { name: "下载预览" }).click();
  const casesDownload = await casesDownloadPromise;
  const casesPath = testInfo.outputPath("spdk_cli_rpc_black_box_cases.json");
  await casesDownload.saveAs(casesPath);
  const casesText = fs.readFileSync(casesPath, "utf8");
  expect(casesText).toContain("black_box_ready");
  expect(casesText).toContain("public workflow");
  expect(casesText).not.toContain(repo);
  const cases = JSON.parse(casesText) as Array<{ steps?: string[] }>;
  expect(cases.length).toBeGreaterThan(0);
  expect(cases[0].steps ?? []).not.toEqual(
    expect.arrayContaining([expect.stringMatching(/rpc_get_methods|bdev_malloc_create|spdk_app_rpc_listen_start/)]),
  );

  await expect(page.getByText(/source_scope:accepted artifact:source_scope\.json/)).toBeVisible();
  await expect(page.getByText(/sfmea:accepted artifact:sfmea\.json/)).toBeVisible();
  await expect(page.getByText(/black_box_cases:ok/)).toBeVisible();
});

test("prevents duplicate workbench smoke E2E probes from a real double click", async ({
  page,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-smoke-probe-")));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "nvmf", "smoke_probe.c"),
    "int nvmf_smoke_probe(void) { return 0; }\n",
    "utf8",
  );

  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "运行驾驶舱" }).hover();
  await page.getByRole("button", { name: "运行驾驶舱" }).click();
  await page.getByLabel("Repo path").fill(repo);
  await page.getByRole("button", { name: "执行器体检" }).hover();
  await page.getByRole("button", { name: "执行器体检" }).click();
  await expect(page.getByRole("heading", { name: "执行器矩阵" })).toBeVisible({
    timeout: 15_000,
  });

  const smokeRequests: string[] = [];
  page.on("request", (req) => {
    if (
      req.method() === "POST" &&
      new URL(req.url()).pathname === "/api/workbench/task-runs/smoke-e2e"
    ) {
      smokeRequests.push(req.url());
    }
  });
  const smokeRequest = page.waitForRequest(
    (req) =>
      req.method() === "POST" &&
      new URL(req.url()).pathname === "/api/workbench/task-runs/smoke-e2e",
  );

  await page.getByRole("button", { name: "全链路烟测" }).hover();
  await page.getByRole("button", { name: "全链路烟测" }).dblclick();
  await smokeRequest;
  await expect(page.getByRole("button", { name: "全链路烟测" })).toBeDisabled();
  await expect(page.getByText(/全链路烟测/).last()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/task:task_run_/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/execution:completed|execution:failed|execution:cancelled/)).toBeVisible({
    timeout: 30_000,
  });
  await expect.poll(() => smokeRequests.length).toBe(1);
});

test("locks artifact previews while a prepared workflow is executing", async ({
  page,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-artifact-busy-")));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "nvmf", "artifact_busy.c"),
    "int nvmf_artifact_busy_probe(void) { return 0; }\n",
    "utf8",
  );

  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
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
  await page.getByLabel("Workflow input analysis_object").fill("lib/nvmf artifact busy");
  await page.getByRole("button", { name: "准备运行" }).hover();
  await page.getByRole("button", { name: "准备运行" }).click();
  await expect(page.getByText(/Task run prepared:/)).toBeVisible({ timeout: 15_000 });

  await page.getByRole("button", { name: "审计产物" }).hover();
  await page.getByRole("button", { name: "审计产物" }).click();
  await expect(page.getByText(/产物已加载:/)).toBeVisible({ timeout: 15_000 });
  const taskBundleArtifact = page.getByRole("button", {
    name: /task_bundle:task_bundle\.json/,
  });
  await expect(taskBundleArtifact).toBeVisible();

  const executeRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      /\/api\/workbench\/task-runs\/[^/]+\/execute$/.test(new URL(request.url()).pathname),
  );
  const executeRequests: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      /\/api\/workbench\/task-runs\/[^/]+\/execute$/.test(new URL(request.url()).pathname)
    ) {
      executeRequests.push(request.url());
    }
  });

  await page.getByRole("button", { name: "执行工作流" }).hover();
  await page.getByRole("button", { name: "执行工作流" }).dblclick();
  await executeRequest;
  await expect(page.getByRole("button", { name: "执行工作流" })).toBeDisabled();
  await expect(taskBundleArtifact).toBeDisabled();
  await expect.poll(() => executeRequests.length).toBe(1);
});

test("prevents duplicate recent task restore requests from a real double click", async ({
  page,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-restore-run-")));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "nvmf", "restore_run.c"),
    "int nvmf_restore_run_probe(void) { return 0; }\n",
    "utf8",
  );

  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
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
  await page.getByLabel("Workflow input analysis_object").fill("lib/nvmf restore run");
  await page.getByRole("button", { name: "准备运行" }).hover();
  await page.getByRole("button", { name: "准备运行" }).click();
  await expect(page.getByText(/Task run prepared:/)).toBeVisible({ timeout: 15_000 });

  const preparedText = await page.locator("body").innerText();
  const taskRunId = preparedText.match(/Task run prepared:\s*(task_run_[a-f0-9]+)/)?.[1] ?? "";
  expect(taskRunId).toMatch(/^task_run_[a-f0-9]+$/);
  const restoreRequests: string[] = [];
  const restorePath = `/api/workbench/task-runs/${taskRunId}`;
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (request.method() === "GET" && url.pathname === restorePath) {
      restoreRequests.push(request.url());
    }
  });

  const recentTaskButton = page.getByRole("button").filter({ hasText: taskRunId }).first();
  await expect(recentTaskButton).toBeVisible();
  const restoreRequest = page.waitForRequest(
    (request) =>
      request.method() === "GET" &&
      new URL(request.url()).pathname === restorePath,
  );
  await recentTaskButton.hover();
  await recentTaskButton.dblclick();
  await restoreRequest;
  await expect(page.getByText(`Task run restored: ${taskRunId}`)).toBeVisible({
    timeout: 15_000,
  });
  await expect.poll(() => restoreRequests.length).toBe(1);
});

test("locks sibling agent-run actions while a real step execution is in flight", async ({
  page,
  request,
}) => {
  test.setTimeout(60_000);
  const unique = Date.now();
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-step-busy-")));
  fs.writeFileSync(path.join(repo, "README.md"), "step busy e2e\n", "utf8");
  const agentScript = path.join(repo, "slow-agent.cjs");
  fs.writeFileSync(
    agentScript,
    [
      "const fs = require('node:fs');",
      "const path = require('node:path');",
      "let stdin = '';",
      "process.stdin.on('data', (chunk) => { stdin += chunk; });",
      "process.stdin.on('end', () => {",
      "  const artifactDir = process.env.CODETALK_AGENT_ARTIFACT_DIR;",
      "  setTimeout(() => {",
      "    fs.writeFileSync(path.join(artifactDir, 'result.json'), JSON.stringify({ status: 'ok', sawRunId: stdin.includes('run_id') }));",
      "  }, 350);",
      "});",
    ].join("\n"),
    "utf8",
  );
  const workflowId = `step_busy_${unique}`;
  const originalSettingsResp = await request.get(`${backendBase}/api/settings/agent-providers`);
  expect(originalSettingsResp.ok()).toBeTruthy();
  const originalSettings = await originalSettingsResp.json();
  const customProviders = [
    ...(originalSettings.external_agent_custom_providers ?? []).filter(
      (provider: { id?: string }) => provider.id !== "slow-agent",
    ),
    {
      id: "slow-agent",
      command: `"${process.execPath}" "${agentScript}"`,
      prompt_transport: "stdin",
      supports_artifact_export: true,
      supports_json_output: true,
    },
  ];
  const settingsResp = await request.put(`${backendBase}/api/settings/agent-providers`, {
    data: {
      ...originalSettings,
      external_agent_custom_providers: customProviders,
    },
  });
  expect(settingsResp.ok()).toBeTruthy();

  try {
    await page.goto("/workbench", { waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: "工作流设计" }).hover();
    await page.getByRole("button", { name: "工作流设计" }).click();
    await page.getByLabel("Workflow JSON").fill(
      JSON.stringify(
        {
          id: workflowId,
          name: "Step Busy E2E",
          version: 1,
          inputs: [{ id: "analysis_object", type: "free_text", required: true }],
          steps: [
            {
              id: "slow_step",
              type: "agent_task",
              provider: "slow-agent",
              required_artifacts: ["result.json"],
              goal: "Write result.json after a short delay.",
            },
          ],
          outputs: [
            {
              id: "result",
              type: "json",
              artifact: "result.json",
              schema: {
                type: "object",
                required: ["status"],
                properties: { status: { type: "string" } },
                additionalProperties: true,
              },
            },
          ],
        },
        null,
        2,
      ),
    );
    await page.getByRole("button", { name: "保存工作流" }).hover();
    await page.getByRole("button", { name: "保存工作流" }).click();
    await expect(page.getByText(`工作流已保存: ${workflowId}`)).toBeVisible({
      timeout: 15_000,
    });

    await page.getByRole("button", { name: "运行驾驶舱" }).hover();
    await page.getByRole("button", { name: "运行驾驶舱" }).click();
    await page.getByLabel("Repo path").fill(repo);
    await page.getByLabel("Workflow input analysis_object").fill("lib/nvmf step busy");
    await page.getByRole("button", { name: "准备运行" }).hover();
    await page.getByRole("button", { name: "准备运行" }).click();
    await expect(page.getByText(/Task run prepared:/)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("slow-agent").first()).toBeVisible();

    const executeButton = page.getByRole("button", { name: "Execute" }).first();
    await expect(executeButton).toBeEnabled({ timeout: 10_000 });
    const executeResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().includes("/api/workbench/task-runs/") &&
        response.url().includes("/agent-runs/") &&
        response.url().endsWith("/execute") &&
        response.status() < 500,
    );
    await executeButton.hover();
    await executeButton.click();

    await expect(executeButton).toBeDisabled();
    await expect(page.getByRole("button", { name: "Validate" }).first()).toBeDisabled();
    await expect(page.getByRole("button", { name: "Materialize" }).first()).toBeDisabled();

    await executeResponse;
    await expect(page.getByText(/Agent run completed:/)).toBeVisible({ timeout: 20_000 });
  } finally {
    await request.put(`${backendBase}/api/settings/agent-providers`, {
      data: originalSettings,
    });
  }
});

test("opens a persisted AI review thread from a prepared workbench run through the real UI", async ({
  page,
  request,
}) => {
  test.setTimeout(60_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-review-run-")));
  const publicRepoLabel = `repo:${path.basename(repo)}`;
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
  expect(conversationFromCreate.initial_context.repo_path).toBe(publicRepoLabel);

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
    task_run_id: taskRunId,
    workspace_id: workspaceId,
    memory_namespace: `workspace:${workspaceId}`,
    repo_path: publicRepoLabel,
    artifact_dir: ".",
    agent_runs_count: 0,
    agent_runs: [],
  });
});

test("prevents duplicate workbench AI review threads from a real double click", async ({
  page,
  request,
}) => {
  test.setTimeout(60_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-review-double-")));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "nvmf", "connect.c"),
    "int nvmf_connect_review_double_target(void) { return 0; }\n",
    "utf8",
  );
  const workspaceId = `ai-review-double-ws-${Date.now()}`;

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
  await page.getByLabel("Workflow input analysis_object").fill("lib/nvmf connect review double");
  await page.getByRole("button", { name: "准备运行" }).hover();
  await page.getByRole("button", { name: "准备运行" }).click();
  await expect(page.getByText(/Task run prepared:/)).toBeVisible({ timeout: 15_000 });
  const preparedText = await page.locator("body").innerText();
  const taskRunId = preparedText.match(/Task run prepared:\s*(task_run_[a-f0-9]+)/)?.[1] ?? "";
  expect(taskRunId).not.toEqual("");

  const createRequests: string[] = [];
  page.on("request", (req) => {
    if (
      req.method() === "POST" &&
      new URL(req.url()).pathname === "/api/ai/conversations"
    ) {
      createRequests.push(req.url());
    }
  });
  const createResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/ai/conversations" &&
      response.status() === 201,
  );

  await page.getByRole("button", { name: "围绕本次运行继续追问" }).hover();
  await page.getByRole("button", { name: "围绕本次运行继续追问" }).dblclick();
  await createResponse;
  await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
  await expect(page.getByText(`workbench_task_run / ${taskRunId}`)).toBeVisible({
    timeout: 15_000,
  });
  await expect.poll(() => createRequests.length).toBe(1);

  const listResponse = await request.get(
    `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}/api/ai/conversations?workspace_id=${encodeURIComponent(workspaceId)}`,
  );
  expect(listResponse.ok()).toBeTruthy();
  const listed = (await listResponse.json()) as {
    items: Array<{ scope_type: string; scope_id: string; workspace_id: string }>;
  };
  expect(
    listed.items.filter(
      (item) =>
        item.scope_type === "workbench_task_run" &&
        item.scope_id === taskRunId &&
        item.workspace_id === workspaceId,
    ),
  ).toHaveLength(1);
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

  fs.unlinkSync(path.join(repo, "lib", "nvmf", "tcp.c"));
  await page.getByRole("button", { name: "源码切片" }).hover();
  await page.getByRole("button", { name: "源码切片" }).click();
  await expect(page.getByText("源码切片已加载: 1")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("file_missing").first()).toBeVisible();
  await expect(page.getByText("verified_current")).toHaveCount(0);
});

test("executes resource leak hunt and previews materialized artifacts through the real workbench UI", async ({
  page,
}, testInfo) => {
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
  const executeRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().includes("/api/workbench/task-runs/") &&
      request.url().endsWith("/execute"),
  );
  await page.getByRole("button", { name: "执行工作流" }).hover();
  await page.getByRole("button", { name: "执行工作流" }).click();
  await executeRequest;
  await expect(page.getByRole("button", { name: "围绕本次运行继续追问" })).toBeDisabled();
  await expect(
    page.getByRole("button", { name: /resource_leak_hunt[\s\S]*task_run_/ }).first(),
  ).toBeDisabled();
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
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载预览" }).hover();
  await page.getByRole("button", { name: "下载预览" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/risk_findings\.json$/);
  expect(download.suggestedFilename()).toContain("steps__hunt_risks__");
  const downloadPath = testInfo.outputPath("risk_findings_preview.json");
  await download.saveAs(downloadPath);
  const downloadedArtifact = fs.readFileSync(downloadPath, "utf8");
  expect(downloadedArtifact).toContain("local-resource-scan");
  expect(downloadedArtifact).toContain("lib/bdev/cleanup.c");
  expect(downloadedArtifact).toContain("failure_mode");
  expect(downloadedArtifact).not.toContain(repo);
  const riskFindings = JSON.parse(downloadedArtifact) as Array<Record<string, unknown>>;
  expect(riskFindings.length).toBeGreaterThan(0);
  const sfmeaFinding = riskFindings[0] as {
    failure_mode?: string;
    cause?: string;
    effect?: string;
    detection?: string;
    severity?: string;
    severity_score?: number;
    occurrence_score?: number;
    detection_score?: number;
    rpn?: number;
    mitigation?: string;
    score_explanation?: string;
    sfmea_source?: string;
    sfmea_scope?: string;
  };
  for (const field of [
    "failure_mode",
    "cause",
    "effect",
    "detection",
    "severity",
    "severity_score",
    "occurrence_score",
    "detection_score",
    "rpn",
    "mitigation",
    "score_explanation",
  ] as const) {
    expect(sfmeaFinding[field], `SFMEA field ${field}`).toBeTruthy();
  }
  expect(sfmeaFinding.rpn).toBe(
    Number(sfmeaFinding.severity_score) *
      Number(sfmeaFinding.occurrence_score) *
      Number(sfmeaFinding.detection_score),
  );
  expect(sfmeaFinding.score_explanation).toContain(`severity=${sfmeaFinding.severity_score}`);
  expect(sfmeaFinding.score_explanation).toContain(`occurrence=${sfmeaFinding.occurrence_score}`);
  expect(sfmeaFinding.score_explanation).toContain(`detection=${sfmeaFinding.detection_score}`);
  expect(sfmeaFinding.mitigation).toMatch(/test\/bdev|black-box|logs|public status|reconnect/i);
  expect(sfmeaFinding.sfmea_source).toBe("local_static_scan");
  expect(sfmeaFinding.sfmea_scope).toBe("lib/bdev/cleanup.c");

  const testHooksArtifact = page
    .getByRole("button")
    .filter({ hasText: /test_hooks\.json/ })
    .first();
  await expect(testHooksArtifact).toBeVisible();
});

test("executes rerun twice from the real workbench UI and keeps distinct history artifacts", async ({
  page,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-rerun-ui-")));
  fs.mkdirSync(path.join(repo, "lib", "bdev"), { recursive: true });
  fs.mkdirSync(path.join(repo, "test", "bdev"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "bdev", "rerun.c"),
    [
      "#include <stdlib.h>",
      "void bdev_rerun_probe(void) {",
      "    void *buf = malloc(64);",
      "    if (!buf) { return; }",
      "    if (spdk_bdev_open_ext(\"Malloc0\", true, NULL, NULL, NULL) != 0) { return; }",
      "    free(buf);",
      "}",
      "",
    ].join("\n"),
    "utf8",
  );

  const latestRerun = async () => {
    const body = await page.locator("body").innerText();
    const rerunIds = [...body.matchAll(/rerun-id:(task_run_[^\s]+)/g)].map((match) => match[1]);
    const artifactPaths = [
      ...body.matchAll(/history-latest:(task_reruns\/[^\s]+task_rerun_execution\.json)/g),
    ].map((match) => match[1]);
    const sequenceMatches = [...body.matchAll(/sequence:(\d+)/g)].map((match) => match[1]);
    return {
      rerunId: rerunIds.at(-1) ?? "",
      artifactPath: artifactPaths.at(-1) ?? "",
      sequence: sequenceMatches.at(-1) ?? "",
    };
  };

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
        target_scope: "lib/bdev rerun",
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

  await page.getByRole("button", { name: "执行工作流" }).hover();
  await page.getByRole("button", { name: "执行工作流" }).click();
  await expect(page.getByText(/Workflow execution completed:/)).toBeVisible({
    timeout: 30_000,
  });

  await page.getByRole("button", { name: "复跑计划" }).hover();
  await page.getByRole("button", { name: "复跑计划" }).click();
  await expect(page.getByText(/can-rerun:true/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/history:0/)).toBeVisible();

  await page.getByRole("button", { name: "执行复跑" }).hover();
  await page.getByRole("button", { name: "执行复跑" }).click();
  await expect(page.getByText(/Rerun execution completed:/)).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByText(/history:1/)).toBeVisible();
  await expect(page.getByText(/rerun-execution:executed workflow:completed/)).toBeVisible();
  await expect(page.getByText(/history-latest:task_reruns\//)).toBeVisible();
  const firstRerun = await latestRerun();
  expect(firstRerun.rerunId).toMatch(/_rerun_1$/);
  expect(firstRerun.sequence).toBe("1");
  expect(firstRerun.artifactPath).toMatch(/task_reruns\/.+_rerun_1\/task_rerun_execution\.json/);

  await page.getByRole("button", { name: "执行复跑" }).hover();
  await page.getByRole("button", { name: "执行复跑" }).click();
  await expect(page.getByText(/Rerun execution completed:/)).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByText(/history:2/)).toBeVisible();
  const secondRerun = await latestRerun();
  expect(secondRerun.rerunId).toMatch(/_rerun_2$/);
  expect(secondRerun.sequence).toBe("2");
  expect(secondRerun.artifactPath).toMatch(/task_reruns\/.+_rerun_2\/task_rerun_execution\.json/);
  expect(secondRerun.rerunId).not.toBe(firstRerun.rerunId);
  expect(secondRerun.artifactPath).not.toBe(firstRerun.artifactPath);
});

test("prevents duplicate task rerun execution requests from a real double click", async ({
  page,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-rerun-double-")));
  fs.mkdirSync(path.join(repo, "lib", "bdev"), { recursive: true });
  fs.mkdirSync(path.join(repo, "test", "bdev"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "bdev", "rerun.c"),
    [
      "#include <stdlib.h>",
      "void bdev_rerun_double_probe(void) {",
      "    void *buf = malloc(128);",
      "    if (!buf) { return; }",
      "    if (spdk_bdev_open_ext(\"Malloc0\", true, NULL, NULL, NULL) != 0) { return; }",
      "    free(buf);",
      "}",
      "",
    ].join("\n"),
    "utf8",
  );

  const latestRerun = async () => {
    const body = await page.locator("body").innerText();
    const rerunIds = [...body.matchAll(/rerun-id:(task_run_[^\s]+)/g)].map((match) => match[1]);
    const artifactPaths = [
      ...body.matchAll(/history-latest:(task_reruns\/[^\s]+task_rerun_execution\.json)/g),
    ].map((match) => match[1]);
    const sequenceMatches = [...body.matchAll(/sequence:(\d+)/g)].map((match) => match[1]);
    return {
      rerunId: rerunIds.at(-1) ?? "",
      artifactPath: artifactPaths.at(-1) ?? "",
      sequence: sequenceMatches.at(-1) ?? "",
    };
  };

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
        target_scope: "lib/bdev rerun double click",
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

  await page.getByRole("button", { name: "执行工作流" }).hover();
  await page.getByRole("button", { name: "执行工作流" }).click();
  await expect(page.getByText(/Workflow execution completed:/)).toBeVisible({
    timeout: 30_000,
  });

  await page.getByRole("button", { name: "复跑计划" }).hover();
  await page.getByRole("button", { name: "复跑计划" }).click();
  await expect(page.getByText(/can-rerun:true/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/history:0/)).toBeVisible();

  const rerunRequests: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      request.url().includes("/api/workbench/task-runs/") &&
      request.url().endsWith("/rerun-plan/execute")
    ) {
      rerunRequests.push(request.url());
    }
  });
  const rerunRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().includes("/api/workbench/task-runs/") &&
      request.url().endsWith("/rerun-plan/execute"),
  );

  await page.getByRole("button", { name: "执行复跑" }).hover();
  await page.getByRole("button", { name: "执行复跑" }).dblclick();
  await rerunRequest;
  await expect(page.getByRole("button", { name: "执行复跑" })).toBeDisabled();
  await expect(page.getByText(/Rerun execution completed:/)).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByText(/history:1/)).toBeVisible();
  await expect(page.getByText(/rerun-execution:executed workflow:completed/)).toBeVisible();
  await expect.poll(() => rerunRequests.length).toBe(1);

  const firstRerun = await latestRerun();
  expect(firstRerun.rerunId).toMatch(/_rerun_1$/);
  expect(firstRerun.sequence).toBe("1");
  expect(firstRerun.artifactPath).toMatch(/task_reruns\/.+_rerun_1\/task_rerun_execution\.json/);
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

  const casesDownloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载预览" }).hover();
  await page.getByRole("button", { name: "下载预览" }).click();
  const casesDownload = await casesDownloadPromise;
  expect(casesDownload.suggestedFilename()).toMatch(/black_box_cases\.json$/);
  const casesDownloadPath = test.info().outputPath("mr_black_box_cases_preview.json");
  await casesDownload.saveAs(casesDownloadPath);
  const downloadedCases = fs.readFileSync(casesDownloadPath, "utf8");
  expect(downloadedCases).toContain("local-mr-blackbox");
  expect(downloadedCases).toContain("lib/nvmf/ctrlr.c");
  expect(downloadedCases).toContain("test/nvmf");
  expect(downloadedCases).toContain("no direct internal function invocation");
  expect(downloadedCases).not.toContain(repo);

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

  await expect(page.getByRole("button", { name: "导入语义" })).toBeEnabled({
    timeout: 15_000,
  });
  await page.getByRole("button", { name: "导入语义" }).hover();
  await page.getByRole("button", { name: "导入语义" }).click();
  await expect(page.getByText(/Semantic outputs imported: \d+, rejected: \d+/)).toBeVisible({
    timeout: 15_000,
  });

  await page.getByRole("button", { name: "证据与语义" }).hover();
  await page.getByRole("button", { name: "证据与语义" }).click();
  await page.getByLabel("Semantic search query").fill("nvmf changed path");
  await page.getByRole("button", { name: "搜索", exact: true }).hover();
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page.getByText("local_mr_black_box_001").first()).toBeVisible({
    timeout: 15_000,
  });
  await expect(
    page.getByText("nvmf changed path black-box regression").first(),
  ).toBeVisible();

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
