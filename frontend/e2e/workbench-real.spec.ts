import { expect, type Locator, type Page, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function dragLocatorCenter(
  page: Page,
  source: Locator,
  target: Locator,
  targetOffset: { x: number; y: number } = { x: 0.5, y: 0.5 },
) {
  const sourceBox = await source.boundingBox();
  const targetBox = await target.boundingBox();
  expect(sourceBox, "drag source must be visible").not.toBeNull();
  expect(targetBox, "drag target must be visible").not.toBeNull();
  if (!sourceBox || !targetBox) return;
  await page.mouse.move(sourceBox.x + sourceBox.width / 2, sourceBox.y + sourceBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(
    targetBox.x + targetBox.width * targetOffset.x,
    targetBox.y + targetBox.height * targetOffset.y,
    { steps: 12 },
  );
  await page.mouse.up();
}

async function connectWorkflowPorts(page: Page, sourceTitle: string, targetTitle: string) {
  const source = page.getByRole("button", {
    name: new RegExp(`^从 ${escapeRegExp(sourceTitle)} 拉出连线$`),
  });
  const target = page.getByRole("button", {
    name: new RegExp(`^连线目标 ${escapeRegExp(targetTitle)}$`),
  });
  await source.hover();
  await source.click();
  await target.hover();
  await target.click();
}

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
    { id: "target_crash_restart_blackbox", label: "target 崩溃/重启恢复黑盒场景" },
    { id: "multi_client_isolation_blackbox", label: "多客户端隔离黑盒场景" },
    { id: "queue_depth_backpressure_blackbox", label: "队列深度/反压黑盒场景" },
    { id: "io_error_injection_retry_blackbox", label: "IO 错误注入/重试黑盒场景" },
    { id: "config_reload_persistence_blackbox", label: "配置重载/持久化黑盒场景" },
    { id: "long_running_resource_leak_blackbox", label: "长跑资源泄漏黑盒场景" },
    { id: "basic_lifecycle_smoke_blackbox", label: "基础生命周期冒烟黑盒场景" },
    { id: "io_stress_performance_blackbox", label: "IO 压力/性能基线黑盒场景" },
    { id: "failure_recovery_soak_blackbox", label: "故障恢复/soak 黑盒场景" },
    { id: "transport_network_partition_blackbox", label: "transport 网络分区黑盒场景" },
    { id: "data_integrity_corruption_blackbox", label: "数据完整性/损坏黑盒场景" },
    { id: "upgrade_compatibility_persistence_blackbox", label: "升级兼容/持久化黑盒场景" },
    { id: "telemetry_metrics_regression_blackbox", label: "遥测/指标回归黑盒场景" },
    { id: "nvmf_subsystem_namespace_acl_blackbox", label: "NVMe-oF subsystem/namespace ACL 黑盒场景" },
    { id: "iscsi_lun_resize_hotplug_blackbox", label: "iSCSI LUN resize/hotplug 黑盒场景" },
    { id: "bdev_crypto_integrity_blackbox", label: "bdev crypto/完整性黑盒场景" },
    { id: "scheduler_qos_fairness_blackbox", label: "scheduler QoS/公平性黑盒场景" },
    { id: "backup_restore_integrity_blackbox", label: "备份/恢复完整性黑盒场景" },
    { id: "nvme_discovery_log_blackbox", label: "NVMe discovery/log 黑盒场景" },
    { id: "iscsi_portal_failover_blackbox", label: "iSCSI portal/failover 黑盒场景" },
    { id: "bdev_zone_append_blackbox", label: "bdev zone append 黑盒场景" },
    { id: "jsonrpc_partial_rollback_blackbox", label: "JSON-RPC 部分失败/回滚黑盒场景" },
    { id: "vfio_user_hotplug_reconnect_blackbox", label: "vfio-user hotplug/reconnect 黑盒场景" },
    { id: "lvol_thin_snapshot_blackbox", label: "lvol thin/snapshot 黑盒场景" },
    { id: "api_contract_negative_blackbox", label: "API 契约负例黑盒场景" },
    { id: "state_persistence_restart_blackbox", label: "状态持久化/重启黑盒场景" },
    { id: "concurrency_isolation_race_blackbox", label: "并发隔离/race 黑盒场景" },
    { id: "performance_capacity_regression_blackbox", label: "性能容量回归黑盒场景" },
    { id: "security_access_control_blackbox", label: "安全访问控制黑盒场景" },
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
      "target_crash_restart_blackbox",
      "multi_client_isolation_blackbox",
      "queue_depth_backpressure_blackbox",
      "io_error_injection_retry_blackbox",
      "config_reload_persistence_blackbox",
      "long_running_resource_leak_blackbox",
      "basic_lifecycle_smoke_blackbox",
      "io_stress_performance_blackbox",
      "failure_recovery_soak_blackbox",
      "transport_network_partition_blackbox",
      "data_integrity_corruption_blackbox",
      "upgrade_compatibility_persistence_blackbox",
      "telemetry_metrics_regression_blackbox",
      "nvmf_subsystem_namespace_acl_blackbox",
      "iscsi_lun_resize_hotplug_blackbox",
      "bdev_crypto_integrity_blackbox",
      "scheduler_qos_fairness_blackbox",
      "backup_restore_integrity_blackbox",
      "nvme_discovery_log_blackbox",
      "iscsi_portal_failover_blackbox",
      "bdev_zone_append_blackbox",
      "jsonrpc_partial_rollback_blackbox",
      "vfio_user_hotplug_reconnect_blackbox",
      "lvol_thin_snapshot_blackbox",
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

test("designer blank workflow drives cockpit inputs and real agent artifacts", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const unique = Date.now();
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-designer-link-")));
  fs.writeFileSync(path.join(repo, "README.md"), "designer cockpit link e2e\n", "utf8");
  const workspaceName = `designer-link-${unique}`;
  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);
  const workspace = (await workspaceResp.json()) as { id: string };

  const workflowId = `designer_link_${unique}`;
  const providerId = `designer-agent-${unique}`;
  const agentScript = path.join(repo, "designer-agent.cjs");
  fs.writeFileSync(
    agentScript,
    [
      "const fs = require('node:fs');",
      "const path = require('node:path');",
      "let stdin = '';",
      "process.stdin.on('data', (chunk) => { stdin += chunk; });",
      "process.stdin.on('end', () => {",
      "  const artifactDir = process.env.CODETALK_AGENT_ARTIFACT_DIR;",
      "  fs.mkdirSync(artifactDir, { recursive: true });",
      "  fs.writeFileSync(path.join(artifactDir, 'designer_result.json'), JSON.stringify({",
      "    status: 'ok',",
      "    provider: 'designer-agent',",
      "    sawNamedInput: stdin.includes('designer cockpit target'),",
      "    sawWorkflowGoal: stdin.includes('Write designer_result.json from the named designer input')",
      "  }));",
      "  console.log(JSON.stringify({ status: 'ok', summary: 'designer-agent completed' }));",
      "});",
    ].join("\n"),
    "utf8",
  );

  const providersResp = await request.get(`${backendBase}/api/settings/agent-providers`);
  expect(providersResp.ok()).toBeTruthy();
  const originalSettings = await providersResp.json();
  const settingsResp = await request.put(`${backendBase}/api/settings/agent-providers`, {
    data: {
      ...originalSettings,
      external_agent_custom_providers: [
        ...(originalSettings.external_agent_custom_providers ?? []).filter(
          (provider: { id?: string }) => provider.id !== providerId,
        ),
        {
          id: providerId,
          command: `"${process.execPath}" "${agentScript}"`,
          prompt_transport: "stdin",
          supports_artifact_export: true,
          supports_json_output: true,
        },
      ],
    },
  });
  expect(settingsResp.ok()).toBeTruthy();

  try {
    await page.goto("/workbench", { waitUntil: "domcontentloaded" });
    await page.getByRole("link", { name: "工作流设计" }).hover();
    await page.getByRole("link", { name: "工作流设计" }).click();

    await page.getByRole("button", { name: "新建空白工作流" }).hover();
    await page.getByRole("button", { name: "新建空白工作流" }).click();
    await expect(page.getByText("已创建空白工作流草稿", { exact: true })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByLabel("Workflow builder id")).toBeVisible({ timeout: 15_000 });

    await page.getByLabel("Workflow builder id").fill(workflowId);
    await page.getByLabel("Workflow builder name").fill("Designer Linked Workflow E2E");
    await page.getByRole("textbox", { name: "Workflow builder provider" }).fill(providerId);
    await page
      .getByLabel("Workflow builder goal")
      .fill("Write designer_result.json from the named designer input.");

    await page.getByLabel("New workflow input name").fill("设计器目标");
    await page.getByLabel("New workflow input id").fill("designer_target");
    await page.getByLabel("New workflow input type").selectOption("free_text");
    await page.getByRole("button", { name: "添加输入契约" }).hover();
    await page.getByRole("button", { name: "添加输入契约" }).click();
    await expect(page.getByText(/设计器目标\s+designer_target:free_text/)).toBeVisible({
      timeout: 15_000,
    });

    await page.getByLabel("New workflow output name").fill("设计器结果");
    await page.getByLabel("New workflow output id").fill("designer_result");
    await page.getByLabel("New workflow output type").selectOption("json");
    await page.getByLabel("New workflow output artifact").fill("designer_result.json");
    await page.getByRole("button", { name: "添加输出契约" }).hover();
    await page.getByRole("button", { name: "添加输出契约" }).click();
    await expect(page.getByText(/设计器结果\s+designer_result\.json/)).toBeVisible({
      timeout: 15_000,
    });

    const saveResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === "/api/workbench/workflows",
    );
    await page.getByRole("button", { name: "保存工作流" }).hover();
    await page.getByRole("button", { name: "保存工作流" }).click();
    const savedWorkflowResponse = await saveResponse;
    expect(savedWorkflowResponse.status()).toBe(201);
    await expect(page.getByText(`工作流已保存: ${workflowId}`)).toBeVisible({
      timeout: 15_000,
    });
    const workflowsAfterSaveResp = await request.get(`${backendBase}/api/workbench/workflows`);
    expect(workflowsAfterSaveResp.ok()).toBeTruthy();
    const workflowsAfterSave = (await workflowsAfterSaveResp.json()) as Array<{ id?: string }>;
    expect(workflowsAfterSave.some((workflow) => workflow.id === workflowId)).toBeTruthy();

    await page.getByRole("link", { name: "运行驾驶舱" }).hover();
    await page.getByRole("link", { name: "运行驾驶舱" }).click();
    await expect(page.getByRole("heading", { name: "运行驾驶舱", exact: true })).toBeVisible();
    const workflowSelect = page.locator('main select[aria-label="工作流"]').first();
    await expect(
      workflowSelect.locator(`option[value="${workflowId}"]`),
    ).toHaveCount(1, { timeout: 15_000 });
    await workflowSelect.selectOption(workflowId, { timeout: 10_000 });
    await expect(workflowSelect).toHaveValue(workflowId, { timeout: 10_000 });
    await page.getByLabel("Workspace selector").selectOption(workspace.id);
    await expect(page.getByLabel("Workflow input designer_target")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByLabel("Workflow input analysis_object")).toHaveCount(0);
    await page.getByLabel("Workflow input designer_target").fill("designer cockpit target");
    await page.getByRole("button", { name: "准备运行" }).hover();
    await page.getByRole("button", { name: "准备运行" }).click();
    await expect(page.getByText(/任务已准备 · task_run_/)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(providerId).first()).toBeVisible();
    await expect(page.getByText("designer_result.json").first()).toBeVisible();

    const executeResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        /\/api\/workbench\/task-runs\/[^/]+\/execute$/.test(new URL(response.url()).pathname) &&
        response.status() < 500,
    );
    await page.getByRole("button", { name: "执行工作流" }).hover();
    await page.getByRole("button", { name: "执行工作流" }).click();
    await executeResponse;

    await expect(page.getByText(new RegExp(`运行完成 · ${workflowId}`))).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText("节点完成 · agent_task")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("designer_result.json").first()).toBeVisible();
  } finally {
    await request.put(`${backendBase}/api/settings/agent-providers`, {
      data: originalSettings,
    });
  }
});

test("designer canvas drag connect properties drive cockpit workflow run", async ({
  page,
  request,
}) => {
  test.setTimeout(120_000);
  const unique = Date.now();
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-canvas-flow-")));
  fs.writeFileSync(path.join(repo, "README.md"), "canvas workflow e2e\n", "utf8");
  const workspaceName = `canvas-flow-${unique}`;
  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);
  const workspace = (await workspaceResp.json()) as { id: string };

  const workflowId = `canvas_flow_${unique}`;
  const providerId = `canvas-agent-${unique}`;
  const agentScript = path.join(repo, "canvas-agent.cjs");
  fs.writeFileSync(
    agentScript,
    [
      "const fs = require('node:fs');",
      "const path = require('node:path');",
      "let stdin = '';",
      "process.stdin.on('data', (chunk) => { stdin += chunk; });",
      "process.stdin.on('end', () => {",
      "  const artifactDir = process.env.CODETALK_AGENT_ARTIFACT_DIR;",
      "  fs.mkdirSync(artifactDir, { recursive: true });",
      "  fs.writeFileSync(path.join(artifactDir, 'canvas_result.json'), JSON.stringify({",
      "    status: 'ok',",
      "    sawCanvasInput: stdin.includes('canvas cockpit target'),",
      "    sawCanvasGoal: stdin.includes('Use the canvas-defined input and write canvas_result.json')",
      "  }));",
      "  console.log(JSON.stringify({ status: 'ok', summary: 'canvas-agent completed' }));",
      "});",
    ].join("\n"),
    "utf8",
  );

  const providersResp = await request.get(`${backendBase}/api/settings/agent-providers`);
  expect(providersResp.ok()).toBeTruthy();
  const originalSettings = await providersResp.json();
  const settingsResp = await request.put(`${backendBase}/api/settings/agent-providers`, {
    data: {
      ...originalSettings,
      external_agent_custom_providers: [
        ...(originalSettings.external_agent_custom_providers ?? []).filter(
          (provider: { id?: string }) => provider.id !== providerId,
        ),
        {
          id: providerId,
          command: `"${process.execPath}" "${agentScript}"`,
          prompt_transport: "stdin",
          supports_artifact_export: true,
          supports_json_output: true,
        },
      ],
    },
  });
  expect(settingsResp.ok()).toBeTruthy();

  try {
    await page.goto("/workbench", { waitUntil: "domcontentloaded" });
    await page.getByRole("link", { name: "工作流设计" }).hover();
    await page.getByRole("link", { name: "工作流设计" }).click();

    await page.getByRole("button", { name: "新建空白工作流" }).hover();
    await page.getByRole("button", { name: "新建空白工作流" }).click();
    await page.getByLabel("Workflow builder id").fill(workflowId);
    await page.getByLabel("Workflow builder name").fill("Canvas Drag Workflow E2E");

    const canvas = page.locator(".ct-workflow-board").first();
    await expect(canvas).toBeVisible({ timeout: 15_000 });
    await dragLocatorCenter(page, page.getByRole("button", { name: /输入模块/ }).first(), canvas, {
      x: 0.18,
      y: 0.3,
    });
    await expect(page.getByLabel("Workflow node contract id")).toBeVisible({ timeout: 15_000 });
    await page.getByLabel("Workflow selected node title").fill("画布分析目标");
    await page.getByLabel("Workflow node contract id").fill("canvas_target");
    await page.getByLabel("Workflow node label").fill("画布分析目标");
    await page.getByLabel("Workflow node input type").selectOption("free_text");

    await dragLocatorCenter(page, page.getByRole("button", { name: /输出模块/ }).first(), canvas, {
      x: 0.42,
      y: 0.42,
    });
    await expect(page.getByLabel("Workflow node artifact")).toBeVisible({ timeout: 15_000 });
    await page.getByLabel("Workflow selected node title").fill("画布结果");
    await page.getByLabel("Workflow node contract id").fill("canvas_result");
    await page.getByLabel("Workflow node label").fill("画布结果");
    await page.getByLabel("Workflow node output type").selectOption("json");
    await page.getByLabel("Workflow node artifact").fill("canvas_result.json");

    await page.locator(".ct-workflow-node").filter({ hasText: "claude-code" }).first().click();
    await page.getByLabel("Workflow selected node title").fill("画布执行 Agent");
    await page.getByLabel("Workflow node contract id").fill("canvas_agent");
    await page.getByLabel("Workflow node agent provider").fill(providerId);
    await page
      .getByLabel("Workflow node agent goal")
      .fill("Use the canvas-defined input and write canvas_result.json.");

    await connectWorkflowPorts(page, "画布分析目标", "画布执行 Agent");
    await expect(page.getByRole("button", { name: /删除连线 画布分析目标 -> 画布执行 Agent/ })).toBeVisible({
      timeout: 15_000,
    });
    await page.getByRole("button", { name: /删除连线 画布分析目标 -> 画布执行 Agent/ }).click();
    await expect(page.getByText("连线已删除: 画布分析目标 -> 画布执行 Agent")).toBeVisible({
      timeout: 15_000,
    });
    await connectWorkflowPorts(page, "画布分析目标", "画布执行 Agent");
    await connectWorkflowPorts(page, "画布执行 Agent", "画布结果");

    const saveResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === "/api/workbench/workflows",
    );
    await page.getByRole("button", { name: "保存工作流" }).hover();
    await page.getByRole("button", { name: "保存工作流" }).click();
    const savedWorkflowResponse = await saveResponse;
    expect(savedWorkflowResponse.status()).toBe(201);
    await expect(page.getByText(`工作流已保存: ${workflowId}`)).toBeVisible({
      timeout: 15_000,
    });

    const savedWorkflowsResp = await request.get(`${backendBase}/api/workbench/workflows`);
    expect(savedWorkflowsResp.ok()).toBeTruthy();
    const savedWorkflows = (await savedWorkflowsResp.json()) as Array<{
      id?: string;
      inputs?: Array<{ id?: string; label?: string }>;
      outputs?: Array<{ id?: string; artifact?: string; from?: string }>;
      steps?: Array<{ id?: string; provider?: string; goal?: string }>;
      ui?: { layout?: { edges?: Array<{ source?: string; target?: string }> } };
    }>;
    const savedWorkflow = savedWorkflows.find((workflow) => workflow.id === workflowId);
    expect(savedWorkflow?.inputs?.map((input) => input.id)).toEqual(["canvas_target"]);
    expect(savedWorkflow?.inputs?.[0]?.label).toBe("画布分析目标");
    expect(savedWorkflow?.steps?.find((step) => step.id === "canvas_agent")?.provider).toBe(
      providerId,
    );
    expect(savedWorkflow?.steps?.find((step) => step.id === "canvas_agent")?.goal).toContain(
      "canvas-defined input",
    );
    expect(savedWorkflow?.outputs?.[0]).toMatchObject({
      id: "canvas_result",
      artifact: "canvas_result.json",
      from: "canvas_agent",
    });
    expect(savedWorkflow?.ui?.layout?.edges ?? []).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ source: expect.stringContaining("input"), target: "agent-task" }),
      ]),
    );

    await page.getByRole("link", { name: "运行驾驶舱" }).hover();
    await page.getByRole("link", { name: "运行驾驶舱" }).click();
    const workflowSelect = page.locator('main select[aria-label="工作流"]').first();
    await expect(workflowSelect.locator(`option[value="${workflowId}"]`)).toHaveCount(1, {
      timeout: 15_000,
    });
    await workflowSelect.selectOption(workflowId);
    await expect(workflowSelect).toHaveValue(workflowId);
    await page.getByLabel("Workspace selector").selectOption(workspace.id);
    await expect(page.getByLabel("Workflow input canvas_target")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByLabel(/Workflow input canvas_input/)).toHaveCount(0);
    await page.getByLabel("Workflow input canvas_target").fill("canvas cockpit target");
    await page.getByRole("button", { name: "准备运行" }).hover();
    await page.getByRole("button", { name: "准备运行" }).click();
    await expect(page.getByText(/任务已准备 · task_run_/)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(providerId).first()).toBeVisible();
    await expect(page.getByText("canvas_result.json").first()).toBeVisible();

    const executeResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        /\/api\/workbench\/task-runs\/[^/]+\/execute$/.test(new URL(response.url()).pathname) &&
        response.status() < 500,
    );
    await page.getByRole("button", { name: "执行工作流" }).hover();
    await page.getByRole("button", { name: "执行工作流" }).click();
    await executeResponse;
    await expect(page.getByText(new RegExp(`运行完成 · ${workflowId}`))).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText("节点完成 · canvas_agent")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("canvas_result.json").first()).toBeVisible();
  } finally {
    await request.put(`${backendBase}/api/settings/agent-providers`, {
      data: originalSettings,
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

  await expect(page.getByText(/任务已准备/)).toBeVisible({ timeout: 15_000 });
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
  await expect(page.getByText(/复跑计划 .*:/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/复跑计划: .* \/ 步骤 \d+/)).toBeVisible();
  await expect(page.getByText(/校验:/)).toBeVisible();
  await expect(page.getByText(/可复跑:/)).toBeVisible();

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

  await expect(page.getByText(/任务已准备/)).toBeVisible({ timeout: 15_000 });
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

test("module analysis real run shows local static scan and review state for empty evidence", async ({
  page,
}) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-empty-module-")));
  const workspaceName = `empty-module-${Date.now()}`;

  await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "创建工作空间" }).hover();
  await page.getByPlaceholder(/项目 A/).fill(workspaceName);
  await page.getByPlaceholder(/本地文件夹路径/).fill(repo);
  await page.getByRole("button", { name: "创建工作空间" }).click();
  await expect(page.getByText(workspaceName)).toBeVisible({ timeout: 30_000 });

  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "运行驾驶舱", exact: true })).toBeVisible();
  await page.getByLabel("工作流").selectOption("module_analysis");
  await page.getByLabel("Workspace selector").selectOption({ label: `${workspaceName} · ${repo}` });
  await page.getByLabel("Workflow input repo_path").selectOption(repo);
  await page
    .getByLabel("Workflow input analysis_object")
    .fill("definitely_missing_storage_module");

  const runRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/workbench/task-runs/run",
  );
  await page.getByRole("button", { name: "创建并运行" }).hover();
  await page.getByRole("button", { name: "创建并运行" }).click();
  await runRequest;

  const runPanel = page.getByLabel("运行结果面板");
  await expect(runPanel.getByText("需要复核 · 模块分析工作流")).toBeVisible({
    timeout: 30_000,
  });
  await expect(runPanel.getByText("完成但信息不足")).toBeVisible();
  await expect(runPanel.getByText("本地静态扫描（无 AI）")).toBeVisible();
  await expect(runPanel.getByText(/未找到匹配源码证据/).first()).toBeVisible();
  await expect(runPanel.getByText("节点完成").first()).toBeVisible();
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
  const coveragePath = path.join(repo, "coverage.info");
  fs.writeFileSync(
    coveragePath,
    "TN:\nSF:lib/nvmf/ctrlr.c\nFN:1,spdk_nvmf_ctrlr_connect\nFNDA:1,spdk_nvmf_ctrlr_connect\nend_of_record\n",
    "utf8",
  );

  const workspaceName = `source-flow-${Date.now()}`;
  await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "创建工作空间" }).hover();
  await page.getByPlaceholder(/项目 A/).fill(workspaceName);
  await page.getByPlaceholder(/本地文件夹路径/).fill(repo);
  await page.getByRole("button", { name: "创建工作空间" }).click();
  await expect(page.getByText(workspaceName)).toBeVisible({ timeout: 30_000 });

  await page.goto("/workbench/designer", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "工作流设计", exact: true })).toBeVisible();
  await page.getByLabel("工作流预设").selectOption("source_flow_sfmea_blackbox");
  await page.getByRole("button", { name: "从模板库导入" }).hover();
  await page.getByRole("button", { name: "从模板库导入" }).click();
  await expect(page.getByText(/已从模板库导入到当前草稿/)).toBeVisible({
    timeout: 15_000,
  });
  await page.getByRole("button", { name: "保存工作流" }).hover();
  await page.getByRole("button", { name: "保存工作流" }).click();
  await expect(page.getByText(/内置模板已另存为自定义工作流/)).toBeVisible({ timeout: 15_000 });

  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "运行驾驶舱", exact: true })).toBeVisible();
  await page.getByLabel("工作流").selectOption("source_flow_sfmea_blackbox_custom");
  await page.getByLabel("Workspace selector").selectOption({ label: `${workspaceName} · ${repo}` });
  await page.getByLabel("Workflow input analysis_object").fill("lib/nvmf connect to IO submit flow");
  await page.getByLabel("Workflow input repo_path").selectOption(repo);
  const coverageInput = page.getByLabel("Workflow input coverage_report");
  if ((await coverageInput.count()) > 0) {
    await coverageInput.fill(coveragePath);
  }
  const semanticInput = page.getByLabel("Workflow input semantic_library_ref");
  if ((await semanticInput.count()) > 0) {
    await semanticInput.fill("SPDK NVMe-oF target semantics");
  }
  const freeformInputs = page.getByLabel("Workflow input inputs");
  if ((await freeformInputs.count()) > 0) {
    await freeformInputs.fill("Use the workspace source files and nvmf shell smoke scenario.");
  }

  await page.getByRole("button", { name: "准备运行" }).hover();
  await page.getByRole("button", { name: "准备运行" }).click();
  await expect(page.getByText(/任务已准备/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("button", { name: "执行工作流" })).toBeEnabled({
    timeout: 15_000,
  });
  await page.getByRole("button", { name: "执行工作流" }).hover();
  await page.getByRole("button", { name: "执行工作流" }).click();
  await expect(page.getByText(/工作流执行已完成/)).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByText(/质量审计 · 可交付/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("button", { name: "生成补证据计划" })).toBeVisible();
  await expect(page.getByRole("button", { name: "只重跑低质量交付件" })).toBeVisible();

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

  await expect(page.getByText(/产物预览已加载: .*black_box_cases\.json/)).toBeVisible();
  await expect(page.getByText(/"case_id": "source_flow_black_box_001"/).first()).toBeVisible();
  await expect(page.getByText(/"case_type": "black_box_ready"/).first()).toBeVisible();
  await expect(page.getByText(/全部运行文件 · 内部诊断 \d+/)).toBeVisible();
});

test("prefills workbench workflow inputs from AI thread task-card query", async ({
  page,
}) => {
  const target = "iSCSI login 灰白盒测试设计，输出 SFMEA 和黑盒测试用例";
  const outputs = "sfmea.json,black_box_cases.json,test_design.md";
  await page.goto(
    `/workbench?workflow=source_flow_sfmea_blackbox&target=${encodeURIComponent(target)}&outputs=${encodeURIComponent(outputs)}`,
    { waitUntil: "domcontentloaded" },
  );

  await expect(page.getByRole("heading", { name: "运行驾驶舱", exact: true })).toBeVisible();
  await expect(page.getByLabel("工作流")).toHaveValue("source_flow_sfmea_blackbox");
  await expect(page.getByLabel("Workflow input analysis_object")).toHaveValue(target);
  await page.getByText("高级输入 JSON").click();
  await expect(page.getByLabel("Inputs JSON")).toHaveValue(/requested_outputs/);
  await expect(page.getByLabel("Inputs JSON")).toHaveValue(/black_box_cases\.json/);
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
  test.setTimeout(90_000);
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

test("runs and cancels a real agent workflow with live cockpit events", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const unique = Date.now();
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-workflow-cancel-")));
  fs.writeFileSync(path.join(repo, "README.md"), "workflow cancel e2e\n", "utf8");
  const workflowId = `workflow_cancel_${unique}`;
  const workspaceName = `workflow-cancel-${unique}`;
  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);
  const workspace = (await workspaceResp.json()) as { id: string };
  const providerId = `cancel-agent-${unique}`;
  const agentScript = path.join(repo, "cancel-agent.cjs");
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
      "    fs.mkdirSync(artifactDir, { recursive: true });",
      "    fs.writeFileSync(path.join(artifactDir, 'result.json'), JSON.stringify({ status: 'ok', provider: 'cancel-agent', sawPrompt: stdin.includes('cancel a real workflow run') }));",
      "    console.log(JSON.stringify({ status: 'ok', summary: 'cancel-agent completed' }));",
      "  }, 30000);",
      "});",
    ].join("\n"),
    "utf8",
  );

  const providersResp = await request.get(`${backendBase}/api/settings/agent-providers`);
  expect(providersResp.ok()).toBeTruthy();
  const originalSettings = await providersResp.json();
  const customProviders = [
    ...(originalSettings.external_agent_custom_providers ?? []).filter(
      (provider: { id?: string }) => provider.id !== providerId,
    ),
    {
      id: providerId,
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
    const workflowResp = await request.post(`${backendBase}/api/workbench/workflows`, {
      data: {
        id: workflowId,
        name: "Workflow Cancel E2E",
        version: 1,
        inputs: [{ id: "analysis_object", type: "free_text", required: true }],
        steps: [
          {
            id: "slow_agent_analysis",
            type: "agent_task",
            provider: providerId,
            required_artifacts: ["result.json"],
            goal: "Keep the workflow running briefly, then write result.json.",
          },
        ],
        outputs: [
          {
            id: "result",
            type: "json",
            artifact: "result.json",
            schema: {
              type: "object",
              required: ["status", "provider"],
              properties: {
                status: { type: "string" },
                provider: { type: "string" },
              },
              additionalProperties: true,
            },
          },
        ],
      },
    });
    expect(workflowResp.status()).toBe(201);

    await page.goto("/workbench", { waitUntil: "domcontentloaded" });
    await page.getByLabel("工作流").selectOption(workflowId);
    await page.getByLabel("Workspace selector").selectOption(workspace.id);
    await page.getByLabel("Workflow input analysis_object").fill("cancel a real workflow run");
    await page.getByRole("button", { name: "准备运行" }).hover();
    await page.getByRole("button", { name: "准备运行" }).click();
    await expect(page.getByText(/任务已准备 · task_run_/)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(providerId).first()).toBeVisible();

    const executeRequest = page.waitForRequest(
      (request) =>
        request.method() === "POST" &&
        /\/api\/workbench\/task-runs\/[^/]+\/execute$/.test(new URL(request.url()).pathname),
    );
    await page.getByRole("button", { name: "执行工作流" }).hover();
    await page.getByRole("button", { name: "执行工作流" }).click();
    await executeRequest;

    await expect(page.getByText(new RegExp(`运行中 · ${workflowId}`))).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText("节点开始 · slow_agent_analysis")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText(providerId).first()).toBeVisible();

    const cancelResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        /\/api\/workbench\/task-runs\/[^/]+\/cancel$/.test(new URL(response.url()).pathname) &&
        response.status() < 500,
    );
    await page.getByRole("button", { name: "取消" }).hover();
    await page.getByRole("button", { name: "取消" }).click();
    await cancelResponse;

    await expect(page.getByText("已取消本次工作流运行", { exact: true })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText("已取消").first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(new RegExp(`运行完成 · ${workflowId}`))).toHaveCount(0);
  } finally {
    await request.put(`${backendBase}/api/settings/agent-providers`, {
      data: originalSettings,
    });
  }
});

test("restores a running agent workflow after refreshing the cockpit", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const unique = Date.now();
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-workflow-refresh-")));
  fs.writeFileSync(path.join(repo, "README.md"), "workflow refresh recovery e2e\n", "utf8");
  const workflowId = `workflow_refresh_${unique}`;
  const workspaceName = `workflow-refresh-${unique}`;
  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);
  const workspace = (await workspaceResp.json()) as { id: string };
  const providerId = `refresh-agent-${unique}`;
  const agentScript = path.join(repo, "refresh-agent.cjs");
  fs.writeFileSync(
    agentScript,
    [
      "const fs = require('node:fs');",
      "const path = require('node:path');",
      "process.stdin.resume();",
      "process.stdin.on('end', () => {",
      "  const artifactDir = process.env.CODETALK_AGENT_ARTIFACT_DIR;",
      "  setTimeout(() => {",
      "    fs.mkdirSync(artifactDir, { recursive: true });",
      "    fs.writeFileSync(path.join(artifactDir, 'result.json'), JSON.stringify({ status: 'ok', provider: 'refresh-agent' }));",
      "    console.log(JSON.stringify({ status: 'ok', summary: 'refresh-agent completed' }));",
      "  }, 20000);",
      "});",
    ].join("\n"),
    "utf8",
  );

  const providersResp = await request.get(`${backendBase}/api/settings/agent-providers`);
  expect(providersResp.ok()).toBeTruthy();
  const originalSettings = await providersResp.json();
  const settingsResp = await request.put(`${backendBase}/api/settings/agent-providers`, {
    data: {
      ...originalSettings,
      external_agent_custom_providers: [
        ...(originalSettings.external_agent_custom_providers ?? []).filter(
          (provider: { id?: string }) => provider.id !== providerId,
        ),
        {
          id: providerId,
          command: `"${process.execPath}" "${agentScript}"`,
          prompt_transport: "stdin",
          supports_artifact_export: true,
          supports_json_output: true,
        },
      ],
    },
  });
  expect(settingsResp.ok()).toBeTruthy();

  try {
    const workflowResp = await request.post(`${backendBase}/api/workbench/workflows`, {
      data: {
        id: workflowId,
        name: "Workflow Refresh E2E",
        version: 1,
        inputs: [{ id: "analysis_object", type: "free_text", required: true }],
        steps: [
          {
            id: "refresh_agent_analysis",
            type: "agent_task",
            provider: providerId,
            required_artifacts: ["result.json"],
            goal: "Stay running long enough for the browser to refresh and restore state.",
          },
        ],
        outputs: [
          {
            id: "result",
            type: "json",
            artifact: "result.json",
            schema: {
              type: "object",
              required: ["status", "provider"],
              properties: {
                status: { type: "string" },
                provider: { type: "string" },
              },
              additionalProperties: true,
            },
          },
        ],
      },
    });
    expect(workflowResp.status()).toBe(201);

    await page.goto("/workbench", { waitUntil: "domcontentloaded" });
    await page.getByLabel("工作流").selectOption(workflowId);
    await page.getByLabel("Workspace selector").selectOption(workspace.id);
    await page.getByLabel("Workflow input analysis_object").fill("refresh recovery workflow run");
    await page.getByRole("button", { name: "准备运行" }).hover();
    await page.getByRole("button", { name: "准备运行" }).click();
    await expect(page.getByText(/任务已准备 · task_run_/)).toBeVisible({ timeout: 15_000 });

    const bodyText = await page.locator("body").innerText();
    const taskRunId = bodyText.match(/任务已准备 · (task_run_[a-f0-9]+)/)?.[1] ?? "";
    expect(taskRunId).toMatch(/^task_run_[a-f0-9]+$/);

    const executeResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith(`/api/workbench/task-runs/${taskRunId}/execute`) &&
        response.status() < 500,
    );
    await page.getByRole("button", { name: "执行工作流" }).hover();
    await page.getByRole("button", { name: "执行工作流" }).click();
    await executeResponse;

    await expect(page.getByText(new RegExp(`运行中 · ${workflowId}`))).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText("节点开始 · refresh_agent_analysis")).toBeVisible({
      timeout: 15_000,
    });

    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.getByText(`任务已恢复 · ${taskRunId}`)).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText(new RegExp(`运行中 · ${workflowId}`))).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText("节点开始 · refresh_agent_analysis")).toBeVisible({
      timeout: 15_000,
    });

    const cancelResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith(`/api/workbench/task-runs/${taskRunId}/cancel`) &&
        response.status() < 500,
    );
    await page.getByRole("button", { name: "取消" }).hover();
    await page.getByRole("button", { name: "取消" }).click();
    await cancelResponse;
    await expect(page.getByText("已取消本次工作流运行", { exact: true })).toBeVisible({
      timeout: 15_000,
    });
  } finally {
    await request.put(`${backendBase}/api/settings/agent-providers`, {
      data: originalSettings,
    });
  }
});

test("shows Chinese failure reason and recovery actions for a real failed agent workflow", async ({
  page,
  request,
}) => {
  test.setTimeout(60_000);
  const unique = Date.now();
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-workflow-failure-")));
  fs.writeFileSync(path.join(repo, "README.md"), "workflow failure e2e\n", "utf8");
  const workflowId = `workflow_failure_${unique}`;
  const workspaceName = `workflow-failure-${unique}`;
  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);
  const workspace = (await workspaceResp.json()) as { id: string };
  const providerId = `failure-agent-${unique}`;
  const agentScript = path.join(repo, "failure-agent.cjs");
  fs.writeFileSync(
    agentScript,
    [
      "process.stdin.resume();",
      "process.stdin.on('end', () => {",
      "  console.error('provider failed with internal English diagnostic: missing artifact');",
      "  process.exit(2);",
      "});",
    ].join("\n"),
    "utf8",
  );

  const providersResp = await request.get(`${backendBase}/api/settings/agent-providers`);
  expect(providersResp.ok()).toBeTruthy();
  const originalSettings = await providersResp.json();
  const settingsResp = await request.put(`${backendBase}/api/settings/agent-providers`, {
    data: {
      ...originalSettings,
      external_agent_custom_providers: [
        ...(originalSettings.external_agent_custom_providers ?? []).filter(
          (provider: { id?: string }) => provider.id !== providerId,
        ),
        {
          id: providerId,
          command: `"${process.execPath}" "${agentScript}"`,
          prompt_transport: "stdin",
          supports_artifact_export: true,
          supports_json_output: true,
        },
      ],
    },
  });
  expect(settingsResp.ok()).toBeTruthy();

  try {
    const workflowResp = await request.post(`${backendBase}/api/workbench/workflows`, {
      data: {
        id: workflowId,
        name: "Workflow Failure E2E",
        version: 1,
        inputs: [{ id: "analysis_object", type: "free_text", required: true }],
        steps: [
          {
            id: "failing_agent_analysis",
            type: "agent_task",
            provider: providerId,
            required_artifacts: ["result.json"],
            goal: "Fail with exit code 2 so the cockpit can explain the failure.",
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
    });
    expect(workflowResp.status()).toBe(201);

    await page.goto("/workbench", { waitUntil: "domcontentloaded" });
    await page.getByLabel("工作流").selectOption(workflowId);
    await page.getByLabel("Workspace selector").selectOption(workspace.id);
    await page.getByLabel("Workflow input analysis_object").fill("failure recovery workflow run");
    await page.getByRole("button", { name: "准备运行" }).hover();
    await page.getByRole("button", { name: "准备运行" }).click();
    await expect(page.getByText(/任务已准备 · task_run_/)).toBeVisible({ timeout: 15_000 });

    const executeResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        /\/api\/workbench\/task-runs\/[^/]+\/execute$/.test(new URL(response.url()).pathname) &&
        response.status() < 500,
    );
    await page.getByRole("button", { name: "执行工作流" }).hover();
    await page.getByRole("button", { name: "执行工作流" }).click();
    await executeResponse;

    await expect(page.getByText(new RegExp(`运行失败 · ${workflowId}`))).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("节点失败 · failing_agent_analysis")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText("失败原因", { exact: true })).toBeVisible();
    await expect(page.getByText(/执行器异常退出，退出码 2。请查看内部诊断确认失败节点/)).toBeVisible();
    await expect(page.getByRole("button", { name: "从失败节点重试" })).toBeVisible();
    await expect(page.getByRole("button", { name: "编辑工作流" })).toBeVisible();

    const diagnostics = page.getByRole("group", { name: "运行详细诊断" });
    await expect(diagnostics).toBeVisible();
    await diagnostics.click();
    await expect(page.getByText(/失败类型:agent_error/)).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText(/下一步:从失败节点重试/)).toBeVisible({
      timeout: 15_000,
    });
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
  await expect(page.getByText("语义文件已导入: 1，被拒绝: 0")).toBeVisible({
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
  await expect(page.getByText(/可复跑:true/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/history:0/)).toBeVisible();

  await page.getByRole("button", { name: "执行复跑" }).hover();
  await page.getByRole("button", { name: "执行复跑" }).click();
  await expect(page.getByText(/Rerun execution completed:/)).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByText(/history:1/)).toBeVisible();
  await expect(page.getByText(/复跑执行:已执行 工作流:已完成/)).toBeVisible();
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
  await expect(page.getByText(/可复跑:true/)).toBeVisible({ timeout: 15_000 });
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
  await expect(page.getByText(/复跑执行:已执行 工作流:已完成/)).toBeVisible();
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
  await expect(page.getByText(/输出已固化 · 证据 \d+ 条/)).toBeVisible({
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
  await expect(page.getByText(/已固化证据:/)).toBeVisible();
  await expect(page.getByText(/声明输出:/)).toBeVisible();
  await expect(page.getByText(/black_box_cases:accepted artifact:black_box_cases\.json/).first()).toBeVisible();
  await expect(page.getByText(/工作流输出 sha:/)).toBeVisible();

  await expect(page.getByRole("button", { name: "导入语义" })).toBeEnabled({
    timeout: 15_000,
  });
  await page.getByRole("button", { name: "导入语义" }).hover();
  await page.getByRole("button", { name: "导入语义" }).click();
  await expect(page.getByText(/语义输出已导入: \d+，被拒绝: \d+/)).toBeVisible({
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
