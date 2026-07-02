import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const frontendOrigin = `http://localhost:${process.env.CODETALK_FRONTEND_PORT ?? "3003"}`;

function corsHeaders(origin = frontendOrigin) {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Content-Type": "application/json",
  };
}

async function routeWorkbenchShell(page: import("@playwright/test").Page) {
  await page.route("**/api/workbench/workflows", async (route) => {
    await route.fulfill({
      json: [],
      headers: corsHeaders(route.request().headers().origin),
    });
  });
  await page.route("**/api/workbench/task-runs", async (route) => {
    await route.fulfill({
      json: { items: [] },
      headers: corsHeaders(route.request().headers().origin),
    });
  });
  await page.route("**/api/workbench/task-runs?*", async (route) => {
    await route.fulfill({
      json: { items: [] },
      headers: corsHeaders(route.request().headers().origin),
    });
  });
  await page.route("**/api/workbench/workflow-presets", async (route) => {
    await route.fulfill({
      json: { items: [] },
      headers: corsHeaders(route.request().headers().origin),
    });
  });
  await page.route("**/api/workbench/provider-capabilities", async (route) => {
    await route.fulfill({
      json: {
        status: "ok",
        providers: [
          {
            provider: "claude-code",
            display_name: "Claude Code",
            owner: "agent_cli",
            status: "configured",
            non_blocking: true,
            codetalk_callable: false,
            agent_owned: true,
            command: ["ccr", "code"],
            fallback_commands: [["claude"]],
            readonly_args: [],
            command_hint_env: "CLAUDE_CODE_COMMAND",
            capabilities: {
              provider: "claude-code",
              supports_mcp: true,
              mcp_profiles: ["codehub-readonly"],
              supports_artifact_export: true,
              supports_json_output: true,
              prompt_transport: "claude_print_arg",
            },
            credential_boundary:
              "Agent CLI owns its own MCP credentials and remote access; CodeTalk only validates returned artifacts.",
            diagnostics: {
              health_endpoint: "/api/tools/claude-code/health",
              startup_probe_endpoint: "/api/tools/claude-code/startup-probe",
              configured_command_text: "ccr code",
              fallback_command_texts: ["claude"],
              prompt_transport: "claude_print_arg",
              startup_probe_transport: "claude_print_arg",
              manual_probe_command:
                "POST /api/tools/claude-code/startup-probe with repo_path, then verify the same backend shell can launch: ccr code",
              mcp_credentials_owner: "agent_cli",
              command_resolution: {
                status: "available",
                configured_command: "claude",
                command: "C:/tools/claude.cmd -p --output-format json",
                path: "C:/tools/claude.cmd",
                launch_kind: "exec",
                used_fallback: true,
                reason: "primary command unavailable; using fallback: claude",
                attempt_count: 2,
              },
              probe_recipe: {
                startup_probe_http:
                  "POST /api/tools/claude-code/startup-probe?repo_path=<repo_path>",
                backend_command: "ccr code",
                fallback_commands: ["claude"],
                command_env: "CLAUDE_CODE_COMMAND",
                command_env_example: "CLAUDE_CODE_COMMAND=ccr code",
                environment_checks: ["PATH", "CCR_CONFIG_PATH", "CLAUDE_CODE_CONFIG_PATH"],
              },
              troubleshooting: [
                "PowerShell profile, PATH, and service account environment may differ from an interactive terminal.",
              ],
            },
            unavailable_behavior: "Workflow continues with diagnostics.",
          },
          {
            provider: "local-search",
            display_name: "Local repo search",
            owner: "codetalk_builtin",
            status: "available",
            non_blocking: true,
            codetalk_callable: true,
            agent_owned: false,
            command: [],
            fallback_commands: [],
            readonly_args: [],
            command_hint_env: "",
            capabilities: {
              provider: "local-search",
              supports_mcp: false,
              mcp_profiles: [],
              supports_artifact_export: false,
              supports_json_output: true,
              prompt_transport: "none",
              supports_source_discovery: true,
              supports_call_graph: false,
              supports_source_slices: true,
              supports_black_box_terms: false,
            },
            credential_boundary:
              "CodeTalk owns this provider and validates any materialized evidence locally.",
            unavailable_behavior: "Always available when the repository path is readable.",
          },
          {
            provider: "corp-agent",
            display_name: "Corp Agent",
            owner: "agent_cli",
            status: "configured",
            non_blocking: true,
            codetalk_callable: false,
            agent_owned: true,
            command: ["corp-agent", "run"],
            fallback_commands: [],
            readonly_args: [],
            env_hint_keys: ["CORP_AGENT_PROFILE"],
            env_hints: {
              CORP_AGENT_PROFILE: "innernet",
            },
            command_hint_env: "EXTERNAL_AGENT_CUSTOM_PROVIDERS",
            capabilities: {
              provider: "corp-agent",
              supports_mcp: true,
              mcp_profiles: ["codehub-readonly"],
              supports_artifact_export: true,
              supports_json_output: true,
              prompt_transport: "stdin",
              env_hint_keys: ["CORP_AGENT_PROFILE"],
            },
            credential_boundary:
              "Corp Agent owns internal credentials; CodeTalk validates returned artifacts.",
            unavailable_behavior: "Workflow continues with diagnostics.",
          },
          {
            provider: "fast-context",
            display_name: "fast-context",
            owner: "codetalk_mcp_bridge",
            status: "bridge_disabled",
            non_blocking: true,
            codetalk_callable: false,
            agent_owned: false,
            command: [],
            fallback_commands: [],
            readonly_args: [],
            command_hint_env: "",
            capabilities: {
              provider: "fast-context",
              supports_mcp: true,
              mcp_profiles: [],
              supports_artifact_export: false,
              supports_json_output: true,
              prompt_transport: "mcp",
            },
            credential_boundary:
              "CodeTalk can call this MCP only when the backend bridge exposes it.",
            diagnostics: {
              owner: "codetalk_mcp_bridge",
              status: "bridge_disabled",
              codetalk_callable: false,
              credential_boundary:
                "CodeTalk can call fast-context only through an exposed backend MCP bridge. Agent CLIs may still call their own MCP servers with their own credentials.",
              troubleshooting: [
                "If AGENTS.md requires fast-context but this bridge is disabled, CodeTalk records the gap and uses local search plus Agent CLI discovery.",
              ],
            },
            unavailable_behavior: "CodeTalk records unavailable and continues.",
          },
        ],
        notes: ["Agent CLI providers may call their own MCP tools."],
      },
      headers: corsHeaders(route.request().headers().origin),
    });
  });
  await page.route("**/api/workbench/workflow-capabilities", async (route) => {
    await route.fulfill({
      json: {
        status: "ok",
        input_types: [
          "coverage_report",
          "file",
          "file_set",
          "free_text",
          "mr_link",
          "patch",
        ],
        input_resolvers: ["agent_mcp", "local", "manual"],
        step_types: ["agent_task", "evidence_validate", "render_report", "semantic_retrieve"],
        output_types: ["json", "markdown", "scope_report", "test_cases"],
        input_features: {
          json_schema_validation: true,
          file_copy_and_hash: true,
          text_extraction_chunks: true,
          agent_owned_mcp_inputs: true,
        },
        output_features: {
          json_schema_validation: true,
          workflow_output_materialization: true,
          semantic_case_import_from_outputs: true,
          sha256_and_size_recorded: true,
        },
        agent_cli_features: {
          agent_owned_mcp_credentials: true,
          provider_selection: true,
          startup_probe: true,
          required_artifacts_validation: true,
          source_slice_second_turn: true,
        },
        semantic_library_import_formats: ["json", "jsonl", "ndjson", "csv", "txt"],
        artifact_contract: {
          required_artifacts: "validated locally before outputs are accepted",
          raw_output: "stored for audit but never accepted as evidence without artifacts",
          workflow_outputs: "collected from declared outputs and checked before acceptance",
        },
      },
      headers: corsHeaders(route.request().headers().origin),
    });
  });
  await page.route("**/api/tools/claude-code/startup-probe*", async (route) => {
    await route.fulfill({
      json: {
        provider: "claude-code",
        healthy: true,
        status: "ok",
        message: "startup_probe_ok via ccr code",
        health: {
          command: "ccr code -- -p",
          launch_kind: "powershell-profile",
          used_fallback: false,
          attempts: [
            {
              command: "ccr code",
              status: "available",
              launch_kind: "powershell-profile",
            },
          ],
        },
      },
      headers: corsHeaders(route.request().headers().origin),
    });
  });
  await page.route("**/api/workbench/input-files/upload", async (route) => {
    await route.fulfill({
      json: {
        kind: "workbench_input_upload",
        upload_id: "input_patch_upload",
        input_id: "patch_file",
        filename: "tls.patch",
        content_type: "text/x-patch",
        size: 24,
        sha256: "abc123",
        path: "input_uploads/input_patch_upload/tls.patch",
        input_payload: {
          path: "input_uploads/input_patch_upload/tls.patch",
        },
      },
      headers: corsHeaders(route.request().headers().origin),
    });
  });
}

async function gotoWorkbench(page: Page) {
  const heading = page.getByRole("heading", { name: "智能体编排台" });
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await page.goto("/workbench", { waitUntil: "domcontentloaded", timeout: 60_000 });
    try {
      await expect(heading).toBeVisible({ timeout: 10_000 });
      return;
    } catch (error) {
      if (attempt === 2) throw error;
      await page.waitForTimeout(1000);
    }
  }
}

async function openWorkbenchView(
  page: Page,
  name: "运行驾驶舱" | "工作流设计" | "证据与语义" | "执行器体检",
) {
  await page.getByRole("button", { name: new RegExp(name) }).click();
  await expect(page.getByRole("button", { name: new RegExp(name) })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
}

function minimalWorkflowDefinition(id: string, name: string) {
  return {
    id,
    name,
    version: 1,
    inputs: [
      { id: "analysis_object", type: "free_text", required: false, role: "target scope" },
      { id: "repo_path", type: "directory", required: true, resolver: "local" },
    ],
    steps: [{ id: "render_report", type: "report_render" }],
    outputs: [{ id: "report", type: "markdown", from: "render_report" }],
    audit: { status: "ok", warnings: [] },
  };
}

test("workflow presets stay visible when non-core diagnostics fail", async ({ page }) => {
  const definitions = [
    minimalWorkflowDefinition("module_analysis", "Module Analysis"),
    minimalWorkflowDefinition("resource_leak_hunt", "Resource Leak and Error Branch Hunt"),
    minimalWorkflowDefinition("mr_blackbox_test", "MR Black-box Test Design"),
    minimalWorkflowDefinition("patch_impact_review", "Patch Impact Review"),
    minimalWorkflowDefinition(
      "source_flow_sfmea_blackbox",
      "Code Analysis -> Flow -> SFMEA -> Black-box Cases",
    ),
    minimalWorkflowDefinition("nvmf_connect_io_blackbox", "NVMe-oF Connect / IO Black-box Scenario"),
    minimalWorkflowDefinition("iscsi_login_session_blackbox", "iSCSI Login / Session Black-box Scenario"),
    minimalWorkflowDefinition("bdev_io_reset_blackbox", "bdev IO / Reset Black-box Scenario"),
    minimalWorkflowDefinition("nvmf_tcp_tls_auth_blackbox", "NVMe/TCP TLS / Authentication Black-box Scenario"),
    minimalWorkflowDefinition("bdev_qos_latency_blackbox", "bdev QoS / Latency Degradation Black-box Scenario"),
    minimalWorkflowDefinition(
      "jsonrpc_concurrency_idempotency_blackbox",
      "JSON-RPC Concurrency / Idempotency Black-box Scenario",
    ),
  ];

  await page.route("**/api/workbench/workflows", async (route) => {
    await route.fulfill({
      json: definitions,
      headers: corsHeaders(route.request().headers().origin),
    });
  });
  await page.route("**/api/workbench/workflow-presets", async (route) => {
    await route.fulfill({
      json: {
        items: definitions.map((definition) => ({
          id: definition.id,
          name: definition.name,
          description: `${definition.name} preset`,
          definition,
        })),
      },
      headers: corsHeaders(route.request().headers().origin),
    });
  });
  await page.route("**/api/workbench/task-runs*", async (route) => {
    await route.fulfill({
      json: { items: [] },
      headers: corsHeaders(route.request().headers().origin),
    });
  });
  await page.route("**/api/workbench/provider-capabilities", async (route) => {
    await route.fulfill({
      status: 500,
      json: { detail: "provider probe failed" },
      headers: corsHeaders(route.request().headers().origin),
    });
  });
  await page.route("**/api/workbench/system-audit", async (route) => {
    await route.fulfill({
      status: 500,
      json: { detail: "system audit failed" },
      headers: corsHeaders(route.request().headers().origin),
    });
  });

  await gotoWorkbench(page);
  await openWorkbenchView(page, "工作流设计");

  await expect(page.getByRole("heading", { name: "工作流编排" })).toBeVisible();
  await expect(page.getByText("工作流已加载，部分诊断数据加载失败")).toBeVisible();
  const presetValues = await page
    .getByLabel("工作流预设")
    .locator("option")
    .evaluateAll((options) => options.map((option) => option.getAttribute("value")));
  expect(presetValues).toEqual(expect.arrayContaining(definitions.map((item) => item.id)));
  await expect(page.getByText("11 个已注册")).toBeVisible();
});

test("agent workbench renders workflow and task-run controls", async ({ page }) => {
  test.setTimeout(60_000);
  await routeWorkbenchShell(page);

  await gotoWorkbench(page);

  await openWorkbenchView(page, "执行器体检");
  await expect(page.getByRole("heading", { name: "执行器矩阵" })).toBeVisible();
  await expect(page.getByText("ccr code", { exact: true }).first()).toBeVisible();
  await expect(
    page.getByText("/api/tools/claude-code/startup-probe", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("claude_print_arg").first()).toBeVisible();
  await expect(page.getByText("解析").first()).toBeVisible();
  await expect(page.getByText("available").first()).toBeVisible();
  await expect(page.getByText("fallback").first()).toBeVisible();
  await expect(page.getByText("launch:exec")).toBeVisible();
  await expect(
    page.getByText("原因: primary command unavailable; using fallback: claude"),
  ).toBeVisible();
  await expect(page.getByText("探测配方")).toBeVisible();
  await expect(page.getByText("后端命令:")).toBeVisible();
  await expect(page.getByText("覆盖环境变量:")).toBeVisible();
  await expect(page.getByText("检查:")).toBeVisible();
  await expect(page.getByText(/PowerShell profile/)).toBeVisible();
  await expect(page.getByText("Agent 持有凭证").first()).toBeVisible();
  await expect(page.getByText("CORP_AGENT_PROFILE")).toBeVisible();
  await expect(page.getByText("CodeTalk 可直接调用").first()).toBeVisible();
  await expect(page.getByText("Local repo search")).toBeVisible();
  await expect(page.getByText("源码发现")).toBeVisible();
  await expect(page.getByText("源码切片")).toBeVisible();
  await expect(page.getByText("fast-context").first()).toBeVisible();
  await expect(page.getByText("codetalk_mcp_bridge")).toBeVisible();
  await page.getByRole("button", { name: "启动探测" }).first().click();
  await expect(page.getByText("启动探测 ok: claude-code")).toBeVisible();
  await expect(page.getByText("探测结果:")).toBeVisible();
  await expect(page.getByText("startup_probe_ok via ccr code")).toBeVisible();
  await expect(page.getByText("探测启动:")).toBeVisible();
  await expect(page.getByText("探测次数:")).toBeVisible();
  await openWorkbenchView(page, "工作流设计");
  await expect(page.getByRole("heading", { name: "工作流编排" })).toBeVisible();
  await expect(page.getByLabel("Workflow builder scenario")).toBeVisible();
  const builderScenarioOptions = await page
    .getByLabel("Workflow builder scenario")
    .locator("option")
    .evaluateAll((options) => options.map((option) => option.getAttribute("value")));
  expect(builderScenarioOptions).toEqual(
    expect.arrayContaining([
      "module_analysis",
      "issue_hunt",
      "mr_blackbox",
      "patch_impact",
      "source_flow_sfmea_blackbox",
      "nvmf_connect_io_blackbox",
      "iscsi_login_session_blackbox",
      "bdev_io_reset_blackbox",
      "rpc_config_negative_blackbox",
      "reactor_thread_poller_blackbox",
      "nvmf_disconnect_reconnect_blackbox",
      "iscsi_auth_failure_blackbox",
      "bdev_failover_resource_blackbox",
      "blobstore_ftl_recovery_blackbox",
      "vhost_vfio_user_lifecycle_blackbox",
      "nvmf_tcp_tls_auth_blackbox",
      "bdev_qos_latency_blackbox",
      "jsonrpc_concurrency_idempotency_blackbox",
      "app_startup_shutdown_smoke_blackbox",
      "nvme_ctrlr_hotplug_reset_blackbox",
      "storage_capacity_enospc_recovery_blackbox",
    ]),
  );
  await expect(page.getByRole("button", { name: "应用预设" })).toBeVisible();
  await expect(page.getByRole("button", { name: "安装预设" })).toBeVisible();
  await expect(page.getByText("codehub-mcp")).toBeVisible();
  await expect(page.getByLabel("Workflow builder provider preset")).toBeVisible();
  await page.getByLabel("Workflow builder provider preset").selectOption("corp-agent");
  await expect(
    page.getByRole("textbox", { name: "Workflow builder provider" }),
  ).toHaveValue("corp-agent");
  await expect(page.getByLabel("Workflow builder evidence mappings")).toHaveValue(
    /"patch_impact_scope"/,
  );
  await expect(page.getByLabel("Workflow builder semantic imports")).toHaveValue(
    /"black_box_cases"/,
  );
  await page.getByLabel("Workflow builder scenario").selectOption("source_flow_sfmea_blackbox");
  await page.getByRole("button", { name: "生成草稿" }).click();
  await expect(page.getByText("工作流草稿已生成: custom_mr_blackbox")).toBeVisible();
  await expect(page.getByText("Draft:ready")).toBeVisible();
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/GitNexus 和 CGC/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"sfmea"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"black_box_cases"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"artifact": "sfmea\.json"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"schema": \{\s+"type": "array"/);
  await page.getByLabel("Workflow builder scenario").selectOption("patch_impact");
  await page.getByRole("button", { name: "生成草稿" }).click();
  await expect(page.getByText("工作流草稿已生成: custom_mr_blackbox")).toBeVisible();
  await expect(page.getByText("Draft:ready")).toBeVisible();
  await expect(page.getByText("输出契约预览")).toBeVisible();
  await expect(page.getByText(/test_cases:test_cases/)).toBeVisible();
  await expect(page.getByText("semantic_import", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"patch_file"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"provider": "corp-agent"/);
  await expect(page.getByLabel("Workflow builder input schemas")).toHaveValue(/"patch_file"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"schema": \{\s+"type": "object"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"required": \[\s+"path"\s+\]/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"before_after_flow"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"render_report"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"evidence_memory"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"semantic_import"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"kind": "patch_impact_scope"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"path_field": "file_path"/);
  await openWorkbenchView(page, "运行驾驶舱");
  await expect(page.getByText("工作流输入")).toBeVisible();
  await page.getByLabel("Workflow input patch_file").fill("E:/patches/tls.patch");
  await page.getByLabel("Upload file for patch_file").setInputFiles({
    name: "tls.patch",
    mimeType: "text/x-patch",
    buffer: Buffer.from("diff --git a/tls.c b/tls.c\n"),
  });
  await expect(page.getByText("Input file uploaded: tls.patch")).toBeVisible();
  await page.getByLabel("Workflow input design_doc").fill("E:/docs/tls-design.md");
  await page.getByLabel("Workflow input analysis_object").fill("nvme-tcp-tls");
  await expect(page.getByLabel("Inputs JSON")).toHaveValue(/"patch_file": \{\s+"path": "input_uploads\/input_patch_upload\/tls\.patch"\s+\}/);
  await expect(page.getByLabel("Inputs JSON")).toHaveValue(/"design_doc": \{\s+"path": "E:\/docs\/tls-design\.md"\s+\}/);
  await expect(page.getByLabel("Inputs JSON")).toHaveValue(/"analysis_object": "nvme-tcp-tls"/);
  await expect(page.getByRole("button", { name: "准备运行" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "执行工作流" })).toBeDisabled();
  await expect(page.getByLabel("Repo path")).toBeVisible();
});

test("agent workbench searches semantic cases and evidence memory", async ({ page }) => {
  await routeWorkbenchShell(page);
  await page.route("**/api/workbench/semantic-cases/search*", async (route) => {
    await route.fulfill({
      headers: corsHeaders(route.request().headers().origin),
      json: {
        items: [
          {
            semantic_id: "sem_1",
            case_id: "nvme_tcp_tls_handshake_fail",
            feature: "NVMe TCP TLS",
            module: "nvmf_tcp",
            test_level: "black_box",
            scenario: "TLS handshake fails and connection is released",
            terms: ["TLS negotiation"],
            tags: ["resource_cleanup"],
            preconditions: "",
            steps: [],
            expected: "",
            assertion_style: "",
            raw: {},
          },
        ],
      },
    });
  });
  await page.route("**/api/workbench/memory/search*", async (route) => {
    await route.fulfill({
      headers: corsHeaders(route.request().headers().origin),
      json: {
        items: [
          {
            evidence_id: "ev_tls_cleanup",
            run_id: "run_tls",
            workspace_id: "ws_tls",
            kind: "source_file",
            subject_key: "nof/nvmf_tcp/transport/tls/tls.c",
            status: "verified_output",
            source: "external_agent",
            path: "nof/nvmf_tcp/transport/tls/tls.c",
            symbol: "nvmf_tcp_tls_handshake",
            reason: "validated TLS source",
            confidence: 0.9,
            text: "nvme tcp tls handshake cleanup",
            provenance: {
              workflow_outputs_artifact: {
                artifact: "workflow_outputs.json",
                sha256: "9999888877776666",
              },
              agent_execution_input: {
                artifact: "agent_runs/discover/execution_input.json",
                sha256: "inputhash1234567890",
              },
              agent_execution_result: {
                artifact: "agent_runs/discover/execution_result.json",
                sha256: "resulthash1234567890",
              },
              agent_replay_plan: {
                artifact: "agent_runs/discover/agent_replay_plan.json",
                sha256: "replayhash1234567890",
              },
            },
            source_slices: [
              {
                slice_id: "slice_tls",
                evidence_id: "ev_tls_cleanup",
                file_path: "nof/nvmf_tcp/transport/tls/tls.c",
                start_line: 10,
                end_line: 18,
                sha256: "slicehash1234567890",
                excerpt: "int nvmf_tcp_tls_handshake(void) { return 0; }",
                created_at: "2026-06-23T00:00:00Z",
              },
            ],
            created_at: "2026-06-23T00:00:00Z",
            updated_at: "2026-06-23T00:00:00Z",
          },
        ],
      },
    });
  });
  await gotoWorkbench(page);
  await openWorkbenchView(page, "证据与语义");
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { name: "证据库" })).toBeVisible();
  await page.getByLabel("Semantic feature").fill("NVMe TCP TLS");
  await page.getByLabel("Semantic module").fill("nvmf_tcp");
  await page
    .getByLabel("Semantic case lines")
    .fill("TLS key rotation fails -> old session remains connected until retry");
  await page.getByRole("button", { name: "生成语义 JSON" }).click();
  await expect(page.getByText("语义导入草稿已生成: 1 cases")).toBeVisible();
  await expect(page.getByLabel("Semantic JSON")).toHaveValue(/"case_id": "nvmf_tcp_tls_key_rotation_fails_1"/);
  await expect(page.getByLabel("Semantic JSON")).toHaveValue(/"old session remains connected until retry"/);
  await expect(page.getByLabel("Semantic JSON")).toHaveValue(/"source_ref": "workbench_semantic_text_import"/);

  await Promise.all([
    page.waitForResponse((response) =>
      response.url().includes("/api/workbench/semantic-cases/search") &&
      response.status() === 200,
    ),
    page.getByRole("button", { name: "搜索", exact: true }).click(),
  ]);
  await expect(page.getByText("语义搜索结果: 1")).toBeVisible();
  await expect(
    page.getByText("TLS handshake fails and connection is released", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("证据库只保存结构化事实")).toBeVisible();
  await Promise.all([
    page.waitForResponse((response) =>
      response.url().includes("/api/workbench/memory/search") &&
      response.status() === 200,
    ),
    page.getByRole("button", { name: "搜索证据" }).click(),
  ]);
  await expect(page.getByText("证据搜索结果: 1")).toBeVisible();
  await expect(page.getByText("nof/nvmf_tcp/transport/tls/tls.c").first()).toBeVisible();
  await expect(page.getByText("Replay: agent_runs/discover/agent_replay_plan.json")).toBeVisible();
  await expect(page.getByText("Input: agent_runs/discover/execution_input.json")).toBeVisible();
  await expect(page.getByText("Result: agent_runs/discover/execution_result.json")).toBeVisible();
  await expect(page.getByText("Output: workflow_outputs.json")).toBeVisible();
  await expect(page.getByText("sha:replayhash12")).toBeVisible();
  await expect(page.getByText("slicehash123")).toBeVisible();
});

test("agent workbench previews task run artifact content", async ({ page }) => {
  await routeWorkbenchShell(page);
  const redactedArtifactSecret = "agent-redacted-artifact-secret";
  await page.route("**/api/workbench/task-runs/prepare", async (route) => {
    await route.fulfill({
      headers: corsHeaders(route.request().headers().origin),
      json: {
        task_run_id: "task_run_preview",
        workflow_id: "mr-blackbox-workflow",
        workspace_id: "manual-workspace",
        repo_path: "E:/repo",
        artifact_dir: "E:/data/workbench/task_runs/task_run_preview",
        workflow_snapshot: {},
        input_snapshot: {},
        task_bundle: {
          input_context: {
            file_count: 1,
            inputs: [
              {
                input_id: "design_doc",
                kind: "file",
                filename: "tls-design.md",
                suffix: ".md",
                chunk_count: 2,
                text_truncated: true,
                parse_warnings: ["preview truncated"],
              },
            ],
          },
          context_bundle: { evidence: [], semantic_cases: [] },
          agent_instructions: { files: [] },
          context_discovery_decision: {
            "fast-context": {
              requested_by_agent_instructions: true,
              codetalk_callable: false,
              fallback_path: ["local_search", "gitnexus", "cgc", "agent_cli"],
              warnings: ["fast-context requested by AGENTS.md but backend MCP bridge is unavailable"],
            },
          },
          provider_readiness: {
            repo: { status: "available" },
            codetalk_providers: {
              "local-search": { status: "available", next_check: "repo readable" },
              gitnexus: {
                status: "missing_config",
                next_check: "POST /api/tools/gitnexus/startup-probe?repo_path=<repo_path>",
              },
              cgc: {
                status: "unavailable",
                next_check: "POST /api/tools/cgc/startup-probe?repo_path=<repo_path>",
              },
            },
            agent_cli_providers: {
              "claude-code": {
                status: "unavailable",
                configured_command: "ccr code",
                used_fallback: true,
                reason: "primary command unavailable; using fallback: claude",
                startup_probe_endpoint: "/api/tools/claude-code/startup-probe",
                manual_probe_command:
                  "POST /api/tools/claude-code/startup-probe with repo_path, then verify the same backend shell can launch: ccr code",
              },
            },
            summary: {
              status: "degraded",
              blocking_reasons: [],
              warnings: [
                "codetalk_provider_unavailable:gitnexus",
                "codetalk_provider_unavailable:cgc",
                "agent_cli_unavailable:claude-code",
              ],
            },
          },
        },
        agent_runs: [],
        created_at: "2026-06-23T00:00:00Z",
      },
    });
  });
  await page.route("**/api/workbench/task-runs/task_run_preview/artifacts", async (route) => {
    await route.fulfill({
      headers: corsHeaders(route.request().headers().origin),
      json: {
        task_run_id: "task_run_preview",
        artifact_dir: "E:/data/workbench/task_runs/task_run_preview",
        artifacts: [
          {
            relative_path: "context_discovery_decision.json",
            path: "E:/data/workbench/task_runs/task_run_preview/context_discovery_decision.json",
            kind: "context_discovery_decision",
            size_bytes: 256,
            sha256: "def456",
            preview: "{\"fast-context\":{\"codetalk_callable\":false}}",
          },
          {
            relative_path: "task_bundle.json",
            path: "E:/data/workbench/task_runs/task_run_preview/task_bundle.json",
            kind: "task_bundle",
            size_bytes: 128,
            sha256: "abc123",
            preview: "{\"workflow_id\":\"mr-blackbox-workflow\"}",
          },
          {
            relative_path: "steps/validate_evidence/evidence_validation.json",
            path: "E:/data/workbench/task_runs/task_run_preview/steps/validate_evidence/evidence_validation.json",
            kind: "evidence_validation",
            size_bytes: 512,
            sha256: "fedcba9876543210",
            preview: "{\"accepted_count\":2,\"rejected_count\":1}",
          },
          {
            relative_path: "workflow_output_materialization.json",
            path: "E:/data/workbench/task_runs/task_run_preview/workflow_output_materialization.json",
            kind: "workflow_output_materialization",
            size_bytes: 384,
            sha256: "aaaaabbbbbcccccdddddeeeeefffff1111122222",
            preview: "{\"evidence_count\":2,\"rejected_outputs\":[{}]}",
          },
          {
            relative_path: "semantic_import_outputs_by_step.json",
            path: "E:/data/workbench/task_runs/task_run_preview/semantic_import_outputs_by_step.json",
            kind: "semantic_import_outputs",
            size_bytes: 256,
            sha256: "bbbbbaaaaacccccdddddeeeeefffff1111122222",
            preview: "{\"design\":[{\"output_id\":\"black_box_cases\"}]}",
          },
          {
            relative_path: "memory_retrieval.json",
            path: "E:/data/workbench/task_runs/task_run_preview/memory_retrieval.json",
            kind: "memory_retrieval",
            size_bytes: 512,
            sha256: "memhash1111222233334444",
            preview: "{\"retrieved_count\":1,\"deployment_retrieved_count\":1}",
          },
          {
            relative_path: "input_materials.json",
            path: "E:/data/workbench/task_runs/task_run_preview/input_materials.json",
            kind: "input_materials",
            size_bytes: 512,
            sha256: "materialhash1111222233334444",
            preview: "{\"kind\":\"input_materials\",\"material_count\":1}",
          },
          {
            relative_path: "black_box_generation_policy.json",
            path: "E:/data/workbench/task_runs/task_run_preview/black_box_generation_policy.json",
            kind: "black_box_generation_policy",
            size_bytes: 512,
            sha256: "ddddccccbbbbaaaa111122223333444455556666",
            preview: "{\"semantic_term_count\":2,\"semantic_terms\":[{\"case_id\":\"TC_TLS\"}]}",
          },
          {
            relative_path: "agent_runs/discover/agent_replay_plan.json",
            path: "E:/data/workbench/task_runs/task_run_preview/agent_runs/discover/agent_replay_plan.json",
            kind: "agent_replay_plan",
            size_bytes: 640,
            sha256: "ccccddddaaaabbbb111122223333444455556666",
            preview: "{\"replay_status\":\"ready\",\"prompt_source\":\"execution_input.json:stdin\"}",
          },
          {
            relative_path: "agent_runs/discover/failure_retry_context.json",
            path: "E:/data/workbench/task_runs/task_run_preview/agent_runs/discover/failure_retry_context.json",
            kind: "agent_failure_retry_context",
            size_bytes: 768,
            sha256: "retryhash111122223333444455556666",
            preview: "{\"kind\":\"agent_failure_retry_context\",\"failure_kind\":\"agent_error\"}",
            preview_redacted: true,
          },
          {
            relative_path: "agent_runs/discover/execution_input.json",
            path: "E:/data/workbench/task_runs/task_run_preview/agent_runs/discover/execution_input.json",
            kind: "agent_execution_input",
            size_bytes: 1024,
            sha256: "inputsha1234567890",
            preview: "{\"provider\":\"claude-code\",\"stdin_redacted\":true}",
          },
        ],
      },
    });
  });
  await page.route(
    "**/api/workbench/task-runs/task_run_preview/artifacts/content/task_bundle.json",
    async (route) => {
      await route.fulfill({
        headers: corsHeaders(route.request().headers().origin),
        json: {
          relative_path: "task_bundle.json",
          path: "E:/data/workbench/task_runs/task_run_preview/task_bundle.json",
          kind: "task_bundle",
          size_bytes: 128,
          sha256: "abc123abc123abc123",
          preview: "{\"workflow_id\":\"mr-blackbox-workflow\"}",
          is_text: true,
          truncated: false,
          content: "{\"workflow_id\":\"mr-blackbox-workflow\",\"provider\":\"claude-code\"}",
        },
      });
    },
  );
  await page.route(
    "**/api/workbench/task-runs/task_run_preview/artifacts/content/steps/validate_evidence/evidence_validation.json",
    async (route) => {
      await route.fulfill({
        headers: corsHeaders(route.request().headers().origin),
        json: {
          relative_path: "steps/validate_evidence/evidence_validation.json",
          path: "E:/data/workbench/task_runs/task_run_preview/steps/validate_evidence/evidence_validation.json",
          kind: "evidence_validation",
          size_bytes: 512,
          sha256: "fedcba9876543210",
          preview: "{\"accepted_count\":2,\"rejected_count\":1}",
          is_text: true,
          truncated: false,
          content: JSON.stringify({
            accepted_count: 2,
            rejected_count: 1,
            accepted_artifact_details: [
              {
                artifact: "source_scope.json",
                source_step_id: "discover",
                sha256: "1111222233334444555566667777888899990000aaaabbbbccccdddd",
                size_bytes: 64,
              },
              {
                artifact: "evidence_cards.json",
                source_step_id: "discover",
                sha256: "aaaabbbbccccdddd1111222233334444555566667777888899990000",
                size_bytes: 128,
              },
            ],
            rejected_artifact_details: [
              {
                artifact: "../secret.txt",
                source_step_id: "discover",
                reason: "invalid_artifact_path",
              },
            ],
          }),
        },
      });
    },
  );
  await page.route(
    "**/api/workbench/task-runs/task_run_preview/artifacts/content/workflow_output_materialization.json",
    async (route) => {
      await route.fulfill({
        headers: corsHeaders(route.request().headers().origin),
        json: {
          relative_path: "workflow_output_materialization.json",
          path: "E:/data/workbench/task_runs/task_run_preview/workflow_output_materialization.json",
          kind: "workflow_output_materialization",
          size_bytes: 384,
          sha256: "aaaaabbbbbcccccdddddeeeeefffff1111122222",
          preview: "{\"evidence_count\":2,\"rejected_outputs\":[{}]}",
          is_text: true,
          truncated: false,
          content: JSON.stringify({
            evidence_count: 2,
            evidence_ids: ["ev1", "ev2"],
            materialized_evidence: [
              {
                evidence_id: "ev1",
                kind: "workflow_output",
                subject_key: "task_run_preview/black_box_cases",
                output_id: "black_box_cases",
                source_step_id: "design",
              },
              {
                evidence_id: "ev2",
                kind: "changed_behavior",
                subject_key: "tls_handshake_retry",
                output_id: "changed_behavior",
                source_step_id: "design",
                mapping_kind: "changed_behavior",
              },
            ],
            rejected_outputs: [
              {
                output: "bad",
                reason: "output_not_ok",
                output_status: "invalid",
                output_reason: "schema_validation_failed",
                schema_errors: ["missing required field: files"],
              },
            ],
            workflow_outputs_artifact: {
              output_count: 3,
              sha256: "9999888877776666555544443333222211110000aaaabbbbccccdddd",
            },
          }),
        },
      });
    },
  );
  await page.route(
    "**/api/workbench/task-runs/task_run_preview/artifacts/content/semantic_import_outputs_by_step.json",
    async (route) => {
      await route.fulfill({
        headers: corsHeaders(route.request().headers().origin),
        json: {
          relative_path: "semantic_import_outputs_by_step.json",
          path: "E:/data/workbench/task_runs/task_run_preview/semantic_import_outputs_by_step.json",
          kind: "semantic_import_outputs",
          size_bytes: 256,
          sha256: "bbbbbaaaaacccccdddddeeeeefffff1111122222",
          preview: "{\"design\":[{\"output_id\":\"black_box_cases\"}]}",
          is_text: true,
          truncated: false,
          content: JSON.stringify({
            design: [
              {
                output_id: "black_box_cases",
                semantic_import: {
                  enabled: true,
                  defaults: {
                    module: "nvmf_tcp_tls",
                  },
                },
              },
            ],
          }),
        },
      });
    },
  );
  await page.route(
    "**/api/workbench/task-runs/task_run_preview/artifacts/content/memory_retrieval.json",
    async (route) => {
      await route.fulfill({
        headers: corsHeaders(route.request().headers().origin),
        json: {
          relative_path: "memory_retrieval.json",
          path: "E:/data/workbench/task_runs/task_run_preview/memory_retrieval.json",
          kind: "memory_retrieval",
          size_bytes: 512,
          sha256: "memhash1111222233334444",
          preview: "{\"retrieved_count\":1,\"deployment_retrieved_count\":1}",
          is_text: true,
          truncated: false,
          content: JSON.stringify({
            provider: "evidence-memory",
            query: "nvme tcp tls",
            retrieved_count: 1,
            deployment_retrieved_count: 1,
            semantic_retrieved_count: 1,
            items: [
              {
                subject_key: "nof/nvmf_tcp/transport/tls/tls.c",
                reuse_reason: "source slices attached and locally verified",
                source_slice_count: 2,
              },
            ],
            deployment_items: [
              {
                subject_key: "claude-code:agent_task_probe",
              },
            ],
            semantic_cases: [
              {
                case_id: "TC_TLS_HANDSHAKE_FAIL",
              },
            ],
          }),
        },
      });
    },
  );
  await page.route(
    "**/api/workbench/task-runs/task_run_preview/artifacts/content/input_materials.json",
    async (route) => {
      await route.fulfill({
        headers: corsHeaders(route.request().headers().origin),
        json: {
          relative_path: "input_materials.json",
          path: "E:/data/workbench/task_runs/task_run_preview/input_materials.json",
          kind: "input_materials",
          size_bytes: 512,
          sha256: "materialhash1111222233334444",
          preview: "{\"kind\":\"input_materials\",\"material_count\":1}",
          is_text: true,
          truncated: false,
          content: JSON.stringify({
            kind: "input_materials",
            material_count: 1,
            read_order: ["design_doc"],
            rules: {
              agent_must_read_materials: true,
              materials_are_source_truth: false,
            },
            materials: [
              {
                input_id: "design_doc",
                material_role: "design context",
                filename: "tls-design.md",
                sha256: "1234567890abcdef1234567890abcdef",
                chunks_path: "E:/data/workbench/task_runs/task_run_preview/inputs/design_doc/chunks.json",
              },
            ],
          }),
        },
      });
    },
  );
  await page.route(
    "**/api/workbench/task-runs/task_run_preview/artifacts/content/black_box_generation_policy.json",
    async (route) => {
      await route.fulfill({
        headers: corsHeaders(route.request().headers().origin),
        json: {
          relative_path: "black_box_generation_policy.json",
          path: "E:/data/workbench/task_runs/task_run_preview/black_box_generation_policy.json",
          kind: "black_box_generation_policy",
          size_bytes: 512,
          sha256: "ddddccccbbbbaaaa111122223333444455556666",
          preview: "{\"semantic_term_count\":2,\"semantic_terms\":[{\"case_id\":\"TC_TLS\"}]}",
          is_text: true,
          truncated: false,
          content: JSON.stringify({
            provider: "semantic-library",
            query: "nvme tcp tls",
            semantic_case_count: 1,
            semantic_term_count: 2,
            semantic_terms: [
              {
                case_id: "TC_TLS_HANDSHAKE_FAIL",
                feature: "NVMe TCP TLS",
                module: "nvmf_tcp",
                terms: ["TLS negotiation", "connection release"],
                test_level: "black_box",
                reuse_rule: "terminology_only_not_source_truth",
              },
            ],
            allowed_uses: [
              "black_box_case_wording",
              "test_taxonomy_alignment",
              "observable_assertion_style",
            ],
            must_not_use_semantics_as: [
              "source_evidence",
              "entry_verification",
              "artifact_validation",
            ],
            authority_rule:
              "semantic-library matches may shape black-box wording but cannot prove source behavior or entry reachability",
          }),
        },
      });
    },
  );
  await page.route(
    "**/api/workbench/task-runs/task_run_preview/artifacts/content/agent_runs/discover/agent_replay_plan.json",
    async (route) => {
      await route.fulfill({
        headers: corsHeaders(route.request().headers().origin),
        json: {
          relative_path: "agent_runs/discover/agent_replay_plan.json",
          path: "E:/data/workbench/task_runs/task_run_preview/agent_runs/discover/agent_replay_plan.json",
          kind: "agent_replay_plan",
          size_bytes: 640,
          sha256: "ccccddddaaaabbbb111122223333444455556666",
          preview: "{\"replay_status\":\"ready\",\"prompt_source\":\"execution_input.json:stdin\"}",
          is_text: true,
          truncated: false,
          content: JSON.stringify({
            replay_status: "ready",
            provider: "claude-code",
            turn_id: "turn_1",
            prompt_source: "execution_input.json:stdin",
            prompt_transport: "stdin",
            cwd: "E:/repo",
            timeout_sec: 90,
            artifact_hashes: {
              "task_bundle.json": "taskhash1234567890",
              "execution_input.json": "inputhash1234567890",
              "agent_output_contract.json": "contracthash1234567890",
            },
            safety_boundary: {
              readonly_env_required: true,
              codetalk_validates_outputs: true,
            },
          }),
        },
      });
    },
  );
  await page.route(
    "**/api/workbench/task-runs/task_run_preview/artifacts/content/agent_runs/discover/failure_retry_context.json",
    async (route) => {
      await route.fulfill({
        headers: corsHeaders(route.request().headers().origin),
        json: {
          relative_path: "agent_runs/discover/failure_retry_context.json",
          path: "E:/data/workbench/task_runs/task_run_preview/agent_runs/discover/failure_retry_context.json",
          kind: "agent_failure_retry_context",
          size_bytes: 768,
          sha256: "retryhash111122223333444455556666",
          preview: "{\"kind\":\"agent_failure_retry_context\",\"failure_kind\":\"agent_error\"}",
          is_text: true,
          truncated: false,
          content_redacted: true,
          content: JSON.stringify({
            kind: "agent_failure_retry_context",
            step_id: "discover",
            failure_kind: "agent_error",
            retryable: true,
            missing_artifacts: ["source_scope.json"],
            previous_execution: {
              status: "error",
              exit_code: 7,
            },
            previous_output: {
              stdout_excerpt: "partial stdout before failure",
              stderr_excerpt: `fatal diagnostic ${redactedArtifactSecret}`,
            },
            retry_instructions: {
              must_produce_artifacts: ["source_scope.json"],
              do_not_repeat: [
                "do not treat raw stdout/stderr as accepted evidence",
                "do not materialize outputs until required artifacts validate",
              ],
            },
          }),
        },
      });
    },
  );
  await page.route(
    "**/api/workbench/task-runs/task_run_preview/artifacts/content/agent_runs/discover/execution_input.json",
    async (route) => {
      await route.fulfill({
        headers: corsHeaders(route.request().headers().origin),
        json: {
          relative_path: "agent_runs/discover/execution_input.json",
          path: "E:/data/workbench/task_runs/task_run_preview/agent_runs/discover/execution_input.json",
          kind: "agent_execution_input",
          size_bytes: 1024,
          sha256: "inputsha1234567890",
          preview: "{\"provider\":\"claude-code\",\"stdin_redacted\":true}",
          is_text: true,
          truncated: false,
          content: JSON.stringify({
            provider: "claude-code",
            turn_id: "turn_1",
            prompt_transport: "stdin",
            prompt_transport_reason: "transport_fallback_from_argv",
            cwd: "E:/repo",
            timeout_sec: 90,
            env_hints: {
              CODETALK_AGENT_READONLY: "1",
            },
            stdin_redacted: true,
            stdin_json_sha256: "stdinsha1234567890",
            agent_output_contract_sha256: "contracthash1234567890",
          }),
        },
      });
    },
  );
  await page.route(
    "**/api/workbench/task-runs/task_run_preview/acceptance-audit",
    async (route) => {
      await route.fulfill({
        headers: corsHeaders(route.request().headers().origin),
        json: {
          task_run_id: "task_run_preview",
          workflow_id: "custom_mr_blackbox",
          workspace_id: "ws-preview",
          status: "incomplete",
          summary: {
            artifact_count: 6,
            required_checks: 12,
            missing_required: 2,
            recommended_checks: 2,
            missing_recommended: 0,
          },
          checks: [],
          missing_required: [
            {
              id: "agent_turn_instruction_policy:discover:turn_1:execution_input",
              status: "missing",
              severity: "required",
              relative_path: "agent_runs/discover/turns/turn_1/execution_input.json",
              kind: "agent_turn_execution_input",
              reason: "agent_instruction_policy_missing",
              expected_files: [
                {
                  relative_path: "AGENTS.md",
                  sha256: "agentinstructions1234567890",
                },
              ],
            },
            {
              id: "agent_turn_stdin_redaction:discover:turn_1:execution_input",
              status: "missing",
              severity: "required",
              relative_path: "agent_runs/discover/turns/turn_1/execution_input.json",
              kind: "agent_turn_execution_input",
              reason: "stdin_redacted_flag_missing",
              stdin_json_sha256: "stdinsha1234567890",
            },
          ],
          missing_recommended: [],
        },
      });
    },
  );

  await gotoWorkbench(page);
  await openWorkbenchView(page, "执行器体检");
  await expect(page.getByText("ccr code", { exact: true }).first()).toBeVisible();
  await openWorkbenchView(page, "运行驾驶舱");
  const preparePanel = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "任务运行" }) });
  const repoInput = preparePanel.getByLabel("Repo path");
  await repoInput.click();
  await repoInput.pressSequentially("E:/repo");
  await expect(repoInput).toHaveValue("E:/repo");
  await expect(preparePanel.getByRole("button", { name: "准备运行" })).toBeEnabled();
  await preparePanel.getByRole("button", { name: "准备运行" }).click();
  await expect(page.getByText("Input context: 1 files")).toBeVisible();
  await expect(page.getByText("tls-design.md")).toBeVisible();
  await expect(page.getByText("chunks:2")).toBeVisible();
  await expect(page.getByText("warnings:preview truncated")).toBeVisible();
  await expect(page.getByText("fast-context: fallback to agent_cli")).toBeVisible();
  await expect(page.getByText("执行器就绪度:")).toBeVisible();
  await expect(page.getByText("degraded")).toBeVisible();
  await expect(page.getByText("gitnexus:missing_config")).toBeVisible();
  await expect(page.getByText("cgc:unavailable")).toBeVisible();
  await expect(page.getByText("claude-code:unavailable")).toBeVisible();
  await expect(
    page.getByText("claude-code command:ccr code fallback", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByText("reason:primary command unavailable; using fallback: claude", {
      exact: false,
    }),
  ).toBeVisible();
  await expect(
    page.getByText("probe:/api/tools/claude-code/startup-probe", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByText("manual:POST /api/tools/claude-code/startup-probe", { exact: false }),
  ).toBeVisible();
  await expect(page.getByText(/审计产物:\s*11/)).toBeVisible();
  await expect(
    page.getByRole("button", {
      name: /agent_failure_retry_context:agent_runs\/discover\/failure_retry_context\.json\s*redacted/,
    }),
  ).toBeVisible();

  await page.getByRole("button", { name: "task_bundle:task_bundle.json" }).click();

  await expect(page.getByText("sha:abc123abc123")).toBeVisible();
  await expect(page.getByText("\"provider\":\"claude-code\"", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "下载预览" })).toBeVisible();

  await page
    .getByRole("button", {
      name: "evidence_validation:steps/validate_evidence/evidence_validation.json",
    })
    .click();

  await expect(page.getByText("Accepted artifacts: 2")).toBeVisible();
  await expect(page.getByText("Rejected artifacts: 1")).toBeVisible();
  await expect(page.getByText("source_scope.json sha:111122223333")).toBeVisible();

  await page
    .getByRole("button", {
      name: "workflow_output_materialization:workflow_output_materialization.json",
    })
    .click();
  await expect(page.getByText("Materialized evidence: 2")).toBeVisible();
  await expect(page.getByText("Rejected outputs: 1")).toBeVisible();
  await expect(page.getByText("Declared outputs: 3")).toBeVisible();
  await expect(page.getByText("First rejected: bad")).toBeVisible();
  await expect(page.getByText("reason:output_not_ok")).toBeVisible();
  await expect(page.getByText("status:invalid")).toBeVisible();
  await expect(page.getByText("schema errors:1")).toBeVisible();
  await expect(page.getByText("workflow_outputs sha:999988887777")).toBeVisible();
  await expect(
    page.getByText("workflow_output:task_run_preview/black_box_cases", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByText("changed_behavior:tls_handshake_retry", { exact: false }),
  ).toBeVisible();
  await expect(page.getByText("mapping:changed_behavior", { exact: false })).toBeVisible();

  await page
    .getByRole("button", {
      name: "semantic_import_outputs:semantic_import_outputs_by_step.json",
    })
    .click();
  await expect(page.getByText("\"output_id\":\"black_box_cases\"", { exact: false })).toBeVisible();
  await expect(page.getByText("\"module\":\"nvmf_tcp_tls\"", { exact: false })).toBeVisible();

  await page
    .getByRole("button", {
      name: "memory_retrieval:memory_retrieval.json",
    })
    .click();
  await expect(page.getByText("Memory retrieval")).toBeVisible();
  await expect(page.getByText("evidence:1")).toBeVisible();
  await expect(page.getByText("deployment:1").first()).toBeVisible();
  await expect(page.getByText("semantics:1")).toBeVisible();
  await expect(page.getByText("slices:2")).toBeVisible();
  await expect(page.getByText("query:nvme tcp tls")).toBeVisible();
  await expect(
    page.getByText("first:nof/nvmf_tcp/transport/tls/tls.c"),
  ).toBeVisible();
  await expect(page.getByText("reuse:source slices attached and locally verified")).toBeVisible();

  await page
    .getByRole("button", {
      name: "input_materials:input_materials.json",
    })
    .click();
  await expect(page.getByText("Input materials")).toBeVisible();
  await expect(page.getByText("materials:1")).toBeVisible();
  await expect(page.getByText("must-read:true")).toBeVisible();
  await expect(page.getByText("source-truth:false")).toBeVisible();
  await expect(page.getByText("read-order:design_doc")).toBeVisible();
  await expect(page.getByText("first:design_doc")).toBeVisible();
  await expect(page.getByText("role:design context")).toBeVisible();
  await expect(page.getByText("file:tls-design.md")).toBeVisible();
  await expect(page.getByText("sha:1234567890ab")).toBeVisible();

  await page
    .getByRole("button", {
      name: "black_box_generation_policy:black_box_generation_policy.json",
    })
    .click();
  await expect(page.getByText("Black-box terms: 2")).toBeVisible();
  await expect(page.getByText("cases:1")).toBeVisible();
  await expect(page.getByText("term:TLS negotiation")).toBeVisible();
  await expect(page.getByText("allowed:black_box_case_wording")).toBeVisible();
  await expect(page.getByText("must-not:source_evidence")).toBeVisible();

  await page
    .getByRole("button", {
      name: "agent_replay_plan:agent_runs/discover/agent_replay_plan.json",
    })
    .click();
  await expect(page.getByText("Replay status: ready")).toBeVisible();
  await expect(page.getByText("provider:claude-code")).toBeVisible();
  await expect(page.getByText("prompt:execution_input.json:stdin")).toBeVisible();
  await expect(page.getByText("readonly:true")).toBeVisible();
  await expect(page.getByText("hashes:3")).toBeVisible();
  await expect(page.getByText("task_bundle sha:taskhash1234")).toBeVisible();

  await page
    .getByRole("button", {
      name: "agent_execution_input:agent_runs/discover/execution_input.json",
    })
    .click();
  await expect(page.getByText("Execution input")).toBeVisible();
  await expect(page.getByText("provider:claude-code")).toBeVisible();
  await expect(page.getByText("transport:stdin")).toBeVisible();
  await expect(page.getByText("reason:transport_fallback_from_argv")).toBeVisible();
  await expect(page.getByText("stdin redacted:true")).toBeVisible();
  await expect(page.getByText("stdin sha:stdinsha1234")).toBeVisible();
  await expect(page.getByText("contract sha:contracthash")).toBeVisible();

  await page
    .getByRole("button", {
      name: "agent_failure_retry_context:agent_runs/discover/failure_retry_context.json",
    })
    .click();
  await expect(page.getByText("Failure retry")).toBeVisible();
  await expect(page.getByText("step:discover")).toBeVisible();
  await expect(page.getByText("kind:agent_error")).toBeVisible();
  await expect(page.getByText("retryable:true")).toBeVisible();
  await expect(page.getByText("exit:7")).toBeVisible();
  await expect(page.getByText("missing:source_scope.json")).toBeVisible();
  await expect(page.getByText("must-produce:source_scope.json")).toBeVisible();
  await expect(
    page.getByText("do-not:do not treat raw stdout/stderr as accepted evidence"),
  ).toBeVisible();
  await expect(page.getByText("redacted", { exact: true }).nth(1)).toBeVisible();
  await expect(page.getByRole("button", { name: "下载脱敏预览" })).toBeVisible();
  await expect(page.locator("body")).not.toContainText(redactedArtifactSecret);

  await page.getByRole("button", { name: "验收审计" }).click();
  await expect(page.getByText("Agent instruction policy")).toBeVisible();
  await expect(page.getByText("reason:agent_instruction_policy_missing")).toBeVisible();
  await expect(page.getByText("expected:AGENTS.md")).toBeVisible();
  await expect(page.getByText("Agent input redaction")).toBeVisible();
  await expect(page.getByText("reason:stdin_redacted_flag_missing")).toBeVisible();
  await expect(page.getByText("stdin-sha:stdinsha1234")).toBeVisible();
});

test("agent workbench prevents duplicate artifact preview requests from a real double click", async ({
  page,
}) => {
  await routeWorkbenchShell(page);
  await page.route("**/api/workbench/task-runs/prepare", async (route) => {
    await route.fulfill({
      headers: corsHeaders(route.request().headers().origin),
      json: {
        task_run_id: "task_run_preview_double",
        workflow_id: "mr-blackbox-workflow",
        workspace_id: "manual-workspace",
        repo_path: "E:/repo",
        artifact_dir: "E:/data/workbench/task_runs/task_run_preview_double",
        workflow_snapshot: {},
        input_snapshot: {},
        task_bundle: {},
        agent_runs: [],
        created_at: "2026-06-23T00:00:00Z",
      },
    });
  });
  await page.route("**/api/workbench/task-runs/task_run_preview_double/artifacts", async (route) => {
    await route.fulfill({
      headers: corsHeaders(route.request().headers().origin),
      json: {
        task_run_id: "task_run_preview_double",
        artifact_dir: "E:/data/workbench/task_runs/task_run_preview_double",
        artifacts: [
          {
            relative_path: "task_bundle.json",
            path: "E:/data/workbench/task_runs/task_run_preview_double/task_bundle.json",
            kind: "task_bundle",
            size_bytes: 128,
            sha256: "abc123abc123abc123",
            preview: "{\"workflow_id\":\"mr-blackbox-workflow\"}",
          },
        ],
      },
    });
  });

  let contentRequests = 0;
  await page.route(
    "**/api/workbench/task-runs/task_run_preview_double/artifacts/content/task_bundle.json",
    async (route) => {
      contentRequests += 1;
      await page.waitForTimeout(250);
      await route.fulfill({
        headers: corsHeaders(route.request().headers().origin),
        json: {
          relative_path: "task_bundle.json",
          path: "E:/data/workbench/task_runs/task_run_preview_double/task_bundle.json",
          kind: "task_bundle",
          size_bytes: 128,
          sha256: "abc123abc123abc123",
          preview: "{\"workflow_id\":\"mr-blackbox-workflow\"}",
          is_text: true,
          truncated: false,
          content: "{\"workflow_id\":\"mr-blackbox-workflow\",\"double_click_safe\":true}",
        },
      });
    },
  );

  await gotoWorkbench(page);
  await openWorkbenchView(page, "运行驾驶舱");
  const preparePanel = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "任务运行" }) });
  await preparePanel.getByLabel("Repo path").fill("E:/repo");
  await preparePanel.getByRole("button", { name: "准备运行" }).hover();
  await preparePanel.getByRole("button", { name: "准备运行" }).click();
  await expect(page.getByText(/审计产物:\s*1/)).toBeVisible();

  const previewButton = page.getByRole("button", { name: "task_bundle:task_bundle.json" });
  await previewButton.hover();
  await previewButton.dblclick();

  await expect(page.getByText("sha:abc123abc123")).toBeVisible();
  await expect(page.getByText("\"double_click_safe\":true", { exact: false })).toBeVisible();
  await expect.poll(() => contentRequests).toBe(1);
});

test("agent workbench opens one AI review thread on double click", async ({ page }) => {
  await routeWorkbenchShell(page);
  let createConversationCalls = 0;

  await page.route("**/api/workbench/task-runs/prepare", async (route) => {
    await route.fulfill({
      headers: corsHeaders(route.request().headers().origin),
      json: {
        task_run_id: "task_run_ai_review",
        workflow_id: "mr-blackbox-workflow",
        workspace_id: "manual-workspace",
        repo_path: "E:/repo",
        artifact_dir: "E:/data/workbench/task_runs/task_run_ai_review",
        workflow_snapshot: {},
        input_snapshot: {},
        task_bundle: {},
        agent_runs: [],
        created_at: "2026-06-23T00:00:00Z",
      },
    });
  });
  await page.route("**/api/workbench/task-runs/task_run_ai_review/artifacts", async (route) => {
    await route.fulfill({
      headers: corsHeaders(route.request().headers().origin),
      json: {
        task_run_id: "task_run_ai_review",
        artifact_dir: "E:/data/workbench/task_runs/task_run_ai_review",
        artifacts: [],
      },
    });
  });
  await page.route("**/api/ai/conversations?*", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 150));
    await route.fulfill({
      headers: corsHeaders(route.request().headers().origin),
      json: { items: [] },
    });
  });
  await page.route("**/api/ai/conversations", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    createConversationCalls += 1;
    await route.fulfill({
      headers: corsHeaders(route.request().headers().origin),
      json: {
        id: "conv-ai-review",
        title: "MR blackbox · AI 复盘",
        scope_type: "workbench_task_run",
        scope_id: "task_run_ai_review",
        workspace_id: "manual-workspace",
        memory_namespace: "workspace:manual-workspace",
        runtime_type: "builtin_llm",
        agent_runtime_id: null,
        latest_run: null,
        created_at: "2026-06-23T00:00:00Z",
        updated_at: "2026-06-23T00:00:00Z",
      },
    });
  });

  await gotoWorkbench(page);
  await openWorkbenchView(page, "运行驾驶舱");
  const repoInput = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "任务运行" }) })
    .getByLabel("Repo path");
  await repoInput.fill("E:/repo");
  await page.getByRole("button", { name: "准备运行" }).click();
  await expect(page.getByRole("paragraph").filter({ hasText: /^task_run_ai_review$/ })).toBeVisible();

  await page.getByRole("button", { name: "围绕本次运行继续追问" }).dblclick();

  await expect.poll(() => createConversationCalls).toBe(1);
});
