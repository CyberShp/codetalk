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
  const savedWorkflows: Array<Record<string, unknown>> = [];
  await page.route("**/api/workbench/workflows/audit-draft", async (route) => {
    const body = route.request().postDataJSON() as {
      outputs?: Array<Record<string, unknown>>;
    };
    const warnings = (body.outputs ?? [])
      .filter((output) => output.type === "json" && typeof output.schema !== "object")
      .map((output) => ({
        severity: "warning",
        code: "json_output_missing_schema",
        path: `outputs.${String(output.id ?? "unknown")}.schema`,
        message: `JSON output ${String(output.id ?? "unknown")} has no schema; structured validation will be limited.`,
      }));
    await route.fulfill({
      json: {
        status: warnings.length ? "warning" : "ok",
        valid: true,
        error: "",
        warnings,
      },
      headers: corsHeaders(route.request().headers().origin),
    });
  });
  await page.route("**/api/workbench/workflows", async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      const saved = {
        ...body,
        id: String(body.id ?? `workflow-${savedWorkflows.length + 1}`),
        audit: {
          status: "ok",
          valid: true,
          error: "",
          warnings: [],
        },
      };
      savedWorkflows.unshift(saved);
      await route.fulfill({
        json: saved,
        headers: corsHeaders(route.request().headers().origin),
      });
      return;
    }
    await route.fulfill({
      json: savedWorkflows,
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
  await page.route("**/api/workspaces", async (route) => {
    await route.fulfill({
      json: [
        {
          id: "ws_spdk",
          name: "spdk",
          repo_path: "/Volumes/Media/dpdk/spdk",
          indexed: 1,
          index_job: null,
          index_progress: 100,
          analyze_status: null,
          analyze_progress: 0,
          last_index_error: null,
          created_at: "2026-07-05T00:00:00Z",
          updated_at: "2026-07-05T00:00:00Z",
          materials: [],
          reports: [],
        },
      ],
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
          skill_injection: true,
        },
        skill_catalog: [
          {
            id: "source-evidence-first",
            label: "源码证据优先",
            source: "codetalk_builtin",
            default_enabled: true,
            description: "先查工作区源码、GitNexus 和 CGC，再生成结论。",
            prompt_hint: "优先读取工作区源码、GitNexus 和 CGC 产物。",
          },
          {
            id: "sfmea",
            label: "SFMEA",
            source: "codetalk_builtin",
            default_enabled: true,
            description: "生成结构化风险评分。",
            prompt_hint: "SFMEA 每条必须包含评分和 mitigation。",
          },
          {
            id: "black-box-test-design",
            label: "黑盒测试设计",
            source: "codetalk_builtin",
            default_enabled: true,
            description: "只描述外部可观测测试步骤。",
            prompt_hint: "黑盒用例不得要求调用内部函数。",
          },
          {
            id: "test-strategy-planning",
            label: "测试策略与计划",
            source: "codetalk_builtin",
            default_enabled: false,
            description: "拆解范围、风险、资源、环境、准入/准出和里程碑。",
            prompt_hint: "输出测试策略和准入准出标准。",
          },
          {
            id: "coverage-gap-analysis",
            label: "覆盖率与缺口分析",
            source: "codetalk_builtin",
            default_enabled: false,
            description: "分析覆盖率和补充建议。",
            prompt_hint: "标出覆盖缺口和证据映射。",
          },
          {
            id: "test-execution-orchestration",
            label: "测试执行编排",
            source: "codetalk_builtin",
            default_enabled: false,
            description: "生成执行矩阵和复跑策略。",
            prompt_hint: "输出可执行测试矩阵。",
          },
          {
            id: "defect-triage-regression",
            label: "缺陷分诊与回归",
            source: "codetalk_builtin",
            default_enabled: false,
            description: "判断缺陷分级和回归范围。",
            prompt_hint: "输出缺陷分级和回归测试范围。",
          },
          {
            id: "performance-reliability-testing",
            label: "性能与可靠性测试",
            source: "codetalk_builtin",
            default_enabled: false,
            description: "覆盖性能、压力、soak 和故障恢复。",
            prompt_hint: "输出性能和可靠性测试计划。",
          },
          {
            id: "artifact-contract",
            label: "产物契约",
            source: "codetalk_builtin",
            default_enabled: true,
            description: "结果必须写入声明的 artifact。",
            prompt_hint: "终端文字不能替代 artifact。",
          },
        ],
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
        input_id: "patch_diff",
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

async function dragWorkflowConnection(page: Page, sourceLabel: string, targetLabel: string) {
  async function scrollPortIntoView(label: string) {
    await page.getByLabel(label).scrollIntoViewIfNeeded();
  }

  await scrollPortIntoView(sourceLabel);
  const source = page.getByLabel(sourceLabel);
  const sourceBox = await source.boundingBox();
  expect(sourceBox).not.toBeNull();
  const sourceHit = await source.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const topElement = document.elementFromPoint(
      rect.left + rect.width / 2,
      rect.top + rect.height / 2,
    );
    return {
      hit: topElement === element || element.contains(topElement),
      tag: topElement?.tagName ?? "",
      label: topElement?.getAttribute("aria-label") ?? "",
      classes: topElement instanceof HTMLElement ? topElement.className : "",
    };
  });
  expect(sourceHit).toEqual(
    expect.objectContaining({
      hit: true,
    }),
  );
  await page.mouse.move(
    sourceBox!.x + sourceBox!.width / 2,
    sourceBox!.y + sourceBox!.height / 2,
  );
  await page.mouse.down();

  await scrollPortIntoView(targetLabel);
  const target = page.getByLabel(targetLabel);
  const targetBox = await target.boundingBox();
  expect(targetBox).not.toBeNull();
  const targetHit = await target.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const topElement = document.elementFromPoint(
      rect.left + rect.width / 2,
      rect.top + rect.height / 2,
    );
    return {
      hit: topElement === element || element.contains(topElement),
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
      tag: topElement?.tagName ?? "",
      label: topElement?.getAttribute("aria-label") ?? "",
      classes: topElement instanceof HTMLElement ? topElement.className : "",
    };
  });
  expect(targetHit).toEqual(
    expect.objectContaining({
      hit: true,
    }),
  );
  await page.mouse.move(
    targetBox!.x + targetBox!.width / 2,
    targetBox!.y + targetBox!.height / 2,
    { steps: 8 },
  );
  await page.mouse.up();
}

async function gotoWorkbench(page: Page) {
  const heading = page.getByRole("heading", { name: "运行驾驶舱" });
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
  name: "运行驾驶舱" | "工作流设计" | "证据与语义",
) {
  type WorkbenchRouteName = "运行驾驶舱" | "工作流设计" | "证据与语义";
  const routes: Record<WorkbenchRouteName, string> = {
    运行驾驶舱: "/workbench",
    工作流设计: "/workbench/designer",
    证据与语义: "/workbench/semantic",
  };
  const headings: Record<WorkbenchRouteName, string> = {
    运行驾驶舱: "运行驾驶舱",
    工作流设计: "工作流设计",
    证据与语义: "语义库",
  };
  await page.goto(routes[name], { waitUntil: "domcontentloaded" });
  await expect(
    page.getByRole("heading", { name: headings[name], exact: true }),
  ).toBeVisible();
}

async function routeSplitPageWorkflowShell(page: Page) {
  const moduleWorkflow = {
    ...minimalWorkflowDefinition("module_analysis", "Module Analysis"),
    inputs: [
      { id: "analysis_object", type: "free_text", required: true, role: "分析目标" },
      { id: "repo_path", type: "directory", required: true, resolver: "local" },
      { id: "requirements_doc", type: "file", required: false, role: "需求文档" },
      { id: "design_doc", type: "file", required: false, role: "设计文档" },
    ],
  };
  const mrWorkflow = {
    ...minimalWorkflowDefinition("mr_blackbox_test", "MR Black-box Test Design"),
    inputs: [
      { id: "mr_link", type: "mr_link", required: true, role: "MR 链接" },
      { id: "patch_diff", type: "patch", required: false, role: "Patch diff" },
      { id: "repo_path", type: "directory", required: true, resolver: "local" },
    ],
  };
  const definitions = [moduleWorkflow, mrWorkflow];
  await routeWorkbenchShell(page);
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

test("orchestration is split into cockpit designer and semantic routes", async ({
  page,
}) => {
  await routeSplitPageWorkflowShell(page);

  await page.goto("/workbench/designer", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "工作流设计" })).toBeVisible();
  await expect(page.getByRole("link", { name: "运行驾驶舱" })).toBeVisible();
  await expect(page.getByRole("link", { name: "工作流设计" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  await expect(page.getByRole("button", { name: /运行驾驶舱/ })).toHaveCount(0);

  await page.getByRole("link", { name: "语义库" }).click();
  await expect(page).toHaveURL(/\/workbench\/semantic$/);
  await expect(page.getByRole("heading", { name: "语义库", exact: true })).toBeVisible();
});

test("workflow designer template controls only affect the draft", async ({
  page,
}) => {
  await routeSplitPageWorkflowShell(page);
  await page.goto("/workbench/designer", { waitUntil: "domcontentloaded" });

  await expect(page.getByRole("button", { name: "从模板库导入" })).toBeVisible();
  await expect(
    page.getByText("导入会替换当前画布草稿，不影响已保存的工作流"),
  ).toBeVisible();
  await expect(page.getByText("保存后才会出现在运行驾驶舱")).toBeVisible();
  await expect(page.getByRole("button", { name: "应用预设" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "安装预设" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "载入所选" })).toHaveCount(0);
});

test("designer preset import does not change cockpit workflow until saved", async ({
  page,
}) => {
  await routeSplitPageWorkflowShell(page);
  await page.goto("/workbench/designer", { waitUntil: "domcontentloaded" });

  await expect(page.getByRole("heading", { name: "工作流设计" })).toBeVisible();
  await page.getByLabel("工作流预设").selectOption("mr_blackbox_test");
  await page.getByRole("button", { name: "从模板库导入" }).click();
  await expect(
    page.getByText(/已从模板库导入到当前草稿: MR (黑盒测试工作流|Black-box Test Design)/),
  ).toBeVisible();

  await page.getByRole("link", { name: "运行驾驶舱" }).click();
  await expect(page).toHaveURL(/\/workbench$/);
  await expect(page.getByLabel("工作流")).toHaveValue("module_analysis");

  const inputRegion = page.getByLabel("Workflow run inputs");
  await expect(inputRegion.getByText("需求文档")).toBeVisible();
  await expect(inputRegion.getByText("设计文档")).toBeVisible();
  await expect(inputRegion.getByText("MR 链接")).toHaveCount(0);
});

test("workflow JSON edits immediately hydrate the canvas draft", async ({
  page,
}) => {
  await routeSplitPageWorkflowShell(page);
  await page.goto("/workbench/designer", { waitUntil: "domcontentloaded" });

  await expect(page.getByRole("heading", { name: "工作流设计" })).toBeVisible();
  await page.locator(".ct-workflow-node").first().click({ position: { x: 44, y: 18 } });
  await expect(page.getByLabel("Workflow inspector")).toBeVisible();
  await page.getByText("高级 Workflow JSON").click();

  const workflow = {
    id: "json_canvas_sync",
    name: "JSON Canvas Sync",
    version: 1,
    inputs: [
      {
        id: "json_input",
        type: "free_text",
        required: true,
        label: "JSON 输入",
      },
    ],
    steps: [
      {
        id: "json_agent",
        type: "agent_task",
        provider: "claude-code",
        goal: "Use the JSON-edited workflow canvas.",
        required_artifacts: ["json_result.json"],
      },
    ],
    outputs: [
      {
        id: "json_output",
        type: "json",
        from: "json_agent",
        artifact: "json_result.json",
        schema: { type: "object" },
      },
    ],
    ui: {
      layout: {
        nodes: [
          {
            id: "json_input_node",
            kind: "input",
            title: "JSON 输入节点",
            subtitle: "from JSON",
            x: 120,
            y: 140,
            source: "canvas",
          },
          {
            id: "json_agent_node",
            kind: "agent",
            title: "JSON Agent 节点",
            subtitle: "from JSON",
            x: 420,
            y: 140,
            source: "canvas",
          },
        ],
        edges: [
          {
            id: "json_edge",
            source: "json_input_node",
            target: "json_agent_node",
            label: "JSON 连线",
          },
        ],
        hidden_node_ids: [],
        hidden_edge_ids: [],
      },
    },
  };

  await page.getByLabel("Workflow JSON").fill(JSON.stringify(workflow, null, 2));

  await expect(
    page.locator(".ct-workflow-node", { hasText: "JSON 输入节点" }),
  ).toBeVisible();
  await expect(
    page.locator(".ct-workflow-node", { hasText: "JSON Agent 节点" }),
  ).toBeVisible();
  await expect(page.getByLabel("Workflow builder id")).toHaveValue("json_canvas_sync");
  await expect(
    page.getByRole("textbox", { name: "Workflow builder provider" }),
  ).toHaveValue("claude-code");
  await expect(page.getByRole("button", { name: "删除连线 JSON 连线" })).toBeVisible();
});

test("cockpit workflow switch rebuilds the visible input form", async ({
  page,
}) => {
  await routeSplitPageWorkflowShell(page);
  await page.goto("/workbench", { waitUntil: "domcontentloaded" });

  await expect(page.getByRole("heading", { name: "运行驾驶舱" })).toBeVisible();
  const inputRegion = page.getByLabel("Workflow run inputs");
  await expect(inputRegion.getByText("需求文档")).toBeVisible();
  await expect(inputRegion.getByText("设计文档")).toBeVisible();
  await expect(inputRegion.getByText("MR 链接")).toHaveCount(0);

  await page.getByLabel("工作流").selectOption("mr_blackbox_test");
  await expect(page.getByText("已随所选工作流更新")).toBeVisible();
  await expect(inputRegion.getByText("MR 链接")).toBeVisible();
  await expect(inputRegion.getByText("Patch diff")).toBeVisible();
  await expect(inputRegion.getByText("需求文档")).toHaveCount(0);
  await expect(page.getByLabel("Advanced workflow JSON")).not.toBeVisible();
});

test("AI thread test activity task card query hydrates cockpit workflow inputs", async ({
  page,
}) => {
  const workflow = {
    ...minimalWorkflowDefinition(
      "source_flow_sfmea_blackbox",
      "Code Analysis -> Flow -> SFMEA -> Black-box Cases",
    ),
    inputs: [
      { id: "analysis_object", type: "free_text", required: true, role: "分析目标" },
      { id: "repo_path", type: "directory", required: true, resolver: "local" },
      { id: "requested_outputs", type: "free_text", required: false, role: "指定输出文件" },
    ],
    outputs: [
      { id: "sfmea", type: "json", from: "render_report", artifact: "sfmea.json" },
      {
        id: "black_box_cases",
        type: "json",
        from: "render_report",
        artifact: "black_box_cases.json",
      },
    ],
  };

  await routeWorkbenchShell(page);
  await page.route("**/api/workbench/workflows", async (route) => {
    await route.fulfill({
      json: [],
      headers: corsHeaders(route.request().headers().origin),
    });
  });
  await page.route("**/api/workbench/workflow-presets", async (route) => {
    await route.fulfill({
      json: {
        items: [
          {
            id: workflow.id,
            name: workflow.name,
            description: "AI 线程测试活动任务卡推荐工作流",
            definition: workflow,
          },
        ],
      },
      headers: corsHeaders(route.request().headers().origin),
    });
  });

  await page.goto(
    "/workbench?workflow=source_flow_sfmea_blackbox&workspace_id=ws_spdk&target=%E9%92%88%E5%AF%B9%20iSCSI%20login%20%E8%BE%93%E5%87%BA%20SFMEA%20%E5%92%8C%E9%BB%91%E7%9B%92%E6%B5%8B%E8%AF%95%E7%94%A8%E4%BE%8B&outputs=sfmea.json,black_box_cases.json",
    { waitUntil: "domcontentloaded" },
  );

  await expect(page.getByRole("heading", { name: "运行驾驶舱" })).toBeVisible();
  await expect(page.getByLabel("工作流")).toHaveValue("source_flow_sfmea_blackbox");
  await expect(page.getByLabel("Workspace selector")).toHaveValue("ws_spdk");
  await expect(page.getByText("源码路径: /Volumes/Media/dpdk/spdk")).toBeVisible();
  await expect(page.getByLabel("Workflow input analysis_object")).toHaveValue(
    "针对 iSCSI login 输出 SFMEA 和黑盒测试用例",
  );
  await expect(page.getByLabel("Workflow input requested_outputs")).toHaveValue(
    "sfmea.json,black_box_cases.json",
  );
  await expect(page.getByText("已随所选工作流更新")).toBeVisible();
});

test("workflow run selector falls back to built-in presets when registered workflows are empty", async ({
  page,
}) => {
  const definitions = [
    minimalWorkflowDefinition("module_analysis", "Module Analysis"),
    minimalWorkflowDefinition("resource_leak_hunt", "Resource Leak and Error Branch Hunt"),
    minimalWorkflowDefinition("mr_blackbox_test", "MR Black-box Test Design"),
    minimalWorkflowDefinition("patch_impact_review", "Patch Impact Review"),
    minimalWorkflowDefinition(
      "source_flow_sfmea_blackbox",
      "Code Analysis -> Flow -> SFMEA -> Black-box Cases",
    ),
    minimalWorkflowDefinition(
      "testing_activity_orchestration",
      "Testing Activity Orchestration",
    ),
    minimalWorkflowDefinition(
      "target_crash_restart_blackbox",
      "Target Crash / Restart Recovery Black-box Scenario",
    ),
    minimalWorkflowDefinition(
      "multi_client_isolation_blackbox",
      "Multi-client Isolation Black-box Scenario",
    ),
    minimalWorkflowDefinition(
      "queue_depth_backpressure_blackbox",
      "Queue Depth / Backpressure Black-box Scenario",
    ),
  ];

  await page.route("**/api/workbench/workflows", async (route) => {
    await route.fulfill({
      json: [],
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
      json: { status: "ok", providers: [], notes: [] },
      headers: corsHeaders(route.request().headers().origin),
    });
  });
  await page.route("**/api/workbench/system-audit", async (route) => {
    await route.fulfill({
      json: { status: "ok", checks: [], summary: {} },
      headers: corsHeaders(route.request().headers().origin),
    });
  });

  await gotoWorkbench(page);
  await expect(page.getByRole("heading", { name: "任务运行" })).toBeVisible();
  await expect(page.locator('select[aria-label="工作流"]')).toBeVisible();
  await expect(
    page.getByLabel("工作流").locator('option[value="module_analysis"]'),
  ).toHaveCount(1);

  const runOptions = await page
    .getByLabel("工作流")
    .locator("option")
    .evaluateAll((options) => options.map((option) => option.textContent?.trim() ?? ""));
  expect(runOptions).toContain("模块分析工作流");
  expect(runOptions).toContain("target 崩溃/重启恢复黑盒场景");
  expect(runOptions).not.toContain("mr-blackbox-workflow");

  await openWorkbenchView(page, "工作流设计");
  await expect(page.getByRole("heading", { name: "工作流编排" })).toBeVisible();
  await expect(
    page.locator('select[aria-label="工作流预设"] option[value="module_analysis"]'),
  ).toHaveCount(1);
  const presetGroups = await page
    .getByLabel("工作流预设")
    .locator("optgroup")
    .evaluateAll((groups) => groups.map((group) => group.getAttribute("label")));
  expect(presetGroups).toEqual(["核心工作流", "常用测试场景"]);
  await expect(
    page.locator('select[aria-label="工作流预设"] option[value="module_analysis"]'),
  ).toHaveCount(1);
  await expect(
    page.locator('select[aria-label="工作流预设"] option[value="target_crash_restart_blackbox"]'),
  ).toHaveCount(1);
});

test("workflow run can select an existing workspace and sync repo_path input", async ({
  page,
}) => {
  const definitions = [minimalWorkflowDefinition("module_analysis", "Module Analysis")];

  await page.route("**/api/workbench/workflows", async (route) => {
    await route.fulfill({
      json: [],
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
      json: { status: "ok", providers: [], notes: [] },
      headers: corsHeaders(route.request().headers().origin),
    });
  });
  await page.route("**/api/workbench/system-audit", async (route) => {
    await route.fulfill({
      json: { status: "ok", checks: [], summary: {} },
      headers: corsHeaders(route.request().headers().origin),
    });
  });
  await page.route("**/api/workspaces", async (route) => {
    await route.fulfill({
      json: [
        {
          id: "ws_one",
          name: "one",
          repo_path: "/tmp/one",
          indexed: 1,
          index_job: null,
          index_progress: 100,
          analyze_status: null,
          analyze_progress: 0,
          last_index_error: null,
          created_at: "2026-07-05T00:00:00Z",
          updated_at: "2026-07-05T00:00:00Z",
          materials: [],
          reports: [],
        },
        {
          id: "ws_spdk",
          name: "spdk",
          repo_path: "/Volumes/Media/dpdk/spdk",
          indexed: 1,
          index_job: null,
          index_progress: 100,
          analyze_status: null,
          analyze_progress: 0,
          last_index_error: null,
          created_at: "2026-07-05T00:00:00Z",
          updated_at: "2026-07-05T00:00:00Z",
          materials: [],
          reports: [],
        },
      ],
      headers: corsHeaders(route.request().headers().origin),
    });
  });

  await gotoWorkbench(page);
  await page.getByLabel("Workspace selector").selectOption("ws_spdk");
  await expect(page.getByLabel("Repo path")).toHaveCount(0);
  await expect(page.getByLabel("Workspace selector")).toHaveValue("ws_spdk");
  await expect(page.getByText("源码路径: /Volumes/Media/dpdk/spdk")).toBeVisible();
  await expect(page.getByText("/Volumes/Media/dpdk/spdk", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Workflow input repo_path")).toHaveCount(0);
});

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
    minimalWorkflowDefinition(
      "testing_activity_orchestration",
      "Testing Activity Orchestration",
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
    minimalWorkflowDefinition("lvol_snapshot_clone_blackbox", "Logical Volume Snapshot / Clone Black-box Scenario"),
    minimalWorkflowDefinition("raid_degraded_rebuild_blackbox", "RAID Degraded / Rebuild Black-box Scenario"),
    minimalWorkflowDefinition("nvme_multipath_failover_blackbox", "NVMe Multipath / Failover Black-box Scenario"),
    minimalWorkflowDefinition("env_hugepage_memory_blackbox", "Environment / Hugepage Memory Black-box Scenario"),
    minimalWorkflowDefinition("spdk_cli_rpc_smoke_blackbox", "SPDK CLI / RPC Smoke Black-box Scenario"),
    minimalWorkflowDefinition("transport_network_partition_blackbox", "Transport Network Partition Black-box Scenario"),
    minimalWorkflowDefinition("data_integrity_corruption_blackbox", "Data Integrity / Corruption Black-box Scenario"),
    minimalWorkflowDefinition(
      "upgrade_compatibility_persistence_blackbox",
      "Upgrade Compatibility / Persistence Black-box Scenario",
    ),
    minimalWorkflowDefinition("telemetry_metrics_regression_blackbox", "Telemetry / Metrics Regression Black-box Scenario"),
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
  await expect(page.getByText("21 个内置模板，21 个已保存")).toBeVisible();
});

test("agent workbench renders workflow and task-run controls", async ({ page }) => {
  test.setTimeout(60_000);
  await routeWorkbenchShell(page);

  await gotoWorkbench(page);

  await expect(page.getByRole("button", { name: /执行器体检/ })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "执行器矩阵" })).toHaveCount(0);
  await openWorkbenchView(page, "工作流设计");
  await expect(page.getByRole("heading", { name: "工作流编排" })).toBeVisible();
  await expect(page.getByLabel("Workflow module palette")).toBeVisible();
  await expect(page.getByLabel("Workflow canvas")).toBeVisible();
  await page.locator(".ct-workflow-node").first().click();
  await expect(page.getByLabel("Workflow inspector")).toBeVisible();
  for (const moduleName of ["输入模块", "智能体模块", "MCP 模块", "Skills 模块", "GitNexus 模块", "CGC 模块", "输出模块"]) {
    await expect(page.getByRole("button", { name: moduleName })).toBeVisible();
  }
  await expect(page.locator(".ct-workflow-link")).toHaveCount(5);
  await expect(page.getByText("repo_path").first()).toBeVisible();
  await expect(page.getByText("sfmea").first()).toBeVisible();
  const canvasMetrics = await page.evaluate(() => {
    const heroTitle = document.querySelector(".ct-workbench-hero h1");
    const canvas = document.querySelector(".ct-workflow-canvas");
    const board = document.querySelector(".ct-workflow-board");
    const firstNode = document.querySelector(".ct-workflow-node");
    const activeTab = document.querySelector(".ct-workbench-tab.is-active");
    const firstPaletteButton = document.querySelector("[aria-label='Workflow module palette'] button");
    const panelTitle = document.querySelector(".ct-workbench-panel-title");
    const nodeBox = firstNode?.getBoundingClientRect();
    const activeTabBox = activeTab?.getBoundingClientRect();
    const paletteButtonBox = firstPaletteButton?.getBoundingClientRect();
    return {
      heroTitleSize: heroTitle ? parseFloat(getComputedStyle(heroTitle).fontSize) : 0,
      panelTitleSize: panelTitle ? parseFloat(getComputedStyle(panelTitle).fontSize) : 0,
      canvasClientWidth: canvas?.clientWidth ?? 0,
      viewportWidth: window.innerWidth,
      boardScrollWidth: board?.scrollWidth ?? 0,
      boardScrollHeight: board?.scrollHeight ?? 0,
      boardClientHeight: board?.clientHeight ?? 0,
      nodeHeight: nodeBox?.height ?? 0,
      activeTabHeight: activeTabBox?.height ?? 0,
      paletteButtonHeight: paletteButtonBox?.height ?? 0,
      paletteFontSize: firstPaletteButton
        ? parseFloat(getComputedStyle(firstPaletteButton).fontSize)
        : 0,
    };
  });
  expect(canvasMetrics.heroTitleSize).toBeLessThanOrEqual(18);
  expect(canvasMetrics.panelTitleSize).toBeLessThanOrEqual(14);
  expect(canvasMetrics.paletteFontSize).toBeLessThanOrEqual(12);
  expect(canvasMetrics.paletteButtonHeight).toBeLessThanOrEqual(42);
  expect(canvasMetrics.nodeHeight).toBeLessThanOrEqual(100);
  expect(canvasMetrics.activeTabHeight).toBeLessThanOrEqual(54);
  expect(canvasMetrics.canvasClientWidth).toBeGreaterThan(canvasMetrics.viewportWidth * 0.56);
  expect(canvasMetrics.boardScrollWidth).toBeGreaterThan(canvasMetrics.canvasClientWidth + 600);
  expect(canvasMetrics.boardScrollHeight).toBeGreaterThan(canvasMetrics.boardClientHeight + 200);
  const inspectorMetrics = await page.getByLabel("Workflow inspector").evaluate((inspector) => {
    const label = inspector.querySelector("label > span");
    const select = inspector.querySelector("select");
    const textarea = inspector.querySelector("textarea");
    const relation = inspector.querySelector("[data-testid='workflow-canvas-relation']");
    return {
      labelSize: label ? parseFloat(getComputedStyle(label).fontSize) : 0,
      selectSize: select ? parseFloat(getComputedStyle(select).fontSize) : 0,
      textareaSize: textarea ? parseFloat(getComputedStyle(textarea).fontSize) : 0,
      relationSize: relation ? parseFloat(getComputedStyle(relation).fontSize) : 0,
      relationText: relation?.textContent ?? "",
      inspectorWidth: inspector.getBoundingClientRect().width,
    };
  });
  expect(inspectorMetrics.labelSize).toBeLessThanOrEqual(11);
  expect(inspectorMetrics.selectSize).toBeLessThanOrEqual(10.5);
  expect(inspectorMetrics.textareaSize).toBeLessThanOrEqual(10.5);
  expect(inspectorMetrics.relationSize).toBeLessThanOrEqual(11);
  expect(inspectorMetrics.inspectorWidth).toBeGreaterThanOrEqual(300);
  expect(inspectorMetrics.relationText).toContain("字段契约");
  expect(inspectorMetrics.relationText).toContain("画布布局");
  await page.getByLabel("关闭属性面板").click();
  await expect(page.getByLabel("Workflow inspector")).toHaveCount(0);
  const firstNode = page.locator(".ct-workflow-node").first();
  const beforeMove = await firstNode.boundingBox();
  expect(beforeMove).not.toBeNull();
  await page.mouse.move(beforeMove!.x + 24, beforeMove!.y + 18);
  await page.mouse.down();
  await page.mouse.move(beforeMove!.x + 146, beforeMove!.y + 78, { steps: 8 });
  await page.mouse.up();
  const afterMove = await firstNode.boundingBox();
  expect(afterMove).not.toBeNull();
  expect(Math.abs(afterMove!.x - beforeMove!.x)).toBeGreaterThan(80);
  expect(Math.abs(afterMove!.y - beforeMove!.y)).toBeGreaterThan(35);
  await page.getByLabel("关闭属性面板").click();
  await expect(page.getByLabel("Workflow inspector")).toHaveCount(0);
  const nodeCountBeforeDrop = await page.locator(".ct-workflow-node").count();
  const boardBox = await page.locator(".ct-workflow-board").boundingBox();
  expect(boardBox).not.toBeNull();
  await page
    .getByLabel("Workflow module palette")
    .getByRole("button", { name: "智能体模块" })
    .dragTo(page.locator(".ct-workflow-board"), {
      targetPosition: { x: Math.min(boardBox!.width - 60, 520), y: 360 },
    });
  await expect(page.locator(".ct-workflow-node")).toHaveCount(nodeCountBeforeDrop + 1);
  await expect(page.getByText(/画布节点已添加/)).toBeVisible();
  await expect(page.getByLabel("Workflow selected node title")).toBeVisible();
  await page.getByLabel("Workflow selected node title").fill("自定义智能体节点");
  const customAgentNode = page.locator(".ct-workflow-node", { hasText: "自定义智能体节点" });
  await expect(customAgentNode).toBeVisible();
  await customAgentNode.click();
  await expect(page.getByLabel("Workflow inspector")).toBeVisible();
  await page.getByLabel("关闭属性面板").click();
  await expect(page.getByLabel("Workflow inspector")).toHaveCount(0);
  await dragWorkflowConnection(page, "从 自定义智能体节点 拉出连线", "连线目标 输出");
  await expect(page.getByText(/连线已添加/)).toBeVisible();
  await expect(page.locator(".ct-workflow-link")).toHaveCount(6);
  const nodeCountBeforeCopy = await page.locator(".ct-workflow-node").count();
  await page.getByRole("button", { name: "复制节点" }).click();
  await expect(page.locator(".ct-workflow-node")).toHaveCount(
    nodeCountBeforeCopy + 1,
  );
  await expect(
    page.locator(".ct-workflow-node", { hasText: "自定义智能体节点 副本" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "删除节点" }).click();
  await expect(page.locator(".ct-workflow-node")).toHaveCount(nodeCountBeforeCopy);
  await page.locator(".ct-workflow-node").first().click();
  await expect(page.getByLabel("Workflow inspector")).toBeVisible();
  await page.getByRole("button", { name: "重置位置" }).click();
  await expect(page.getByText(/节点位置已重置|节点布局已重置/)).toBeVisible();
  await page.getByLabel("关闭属性面板").click();
  await expect(page.getByLabel("Workflow inspector")).toHaveCount(0);
  await dragWorkflowConnection(page, "从 输入 拉出连线", "连线目标 claude-code");
  await expect(page.getByText(/连线已添加: 输入 ->/)).toBeVisible();
  await expect(page.getByLabel("Workflow builder scenario")).toBeVisible();
  const builderScenarioOptions = await page
    .getByLabel("Workflow builder scenario")
    .locator("option")
    .evaluateAll((options) => options.map((option) => option.getAttribute("value")));
  expect(builderScenarioOptions).toEqual(
    expect.arrayContaining([
      "module_analysis",
      "resource_leak_hunt",
      "mr_blackbox_test",
      "testing_activity_orchestration",
      "patch_impact_review",
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
      "nvmf_rdma_transport_blackbox",
      "iscsi_digest_multi_connection_blackbox",
      "bdev_hotremove_io_error_blackbox",
      "blobstore_metadata_powerfail_blackbox",
      "rpc_security_authz_blackbox",
      "lvol_snapshot_clone_blackbox",
      "raid_degraded_rebuild_blackbox",
      "nvme_multipath_failover_blackbox",
      "env_hugepage_memory_blackbox",
      "spdk_cli_rpc_smoke_blackbox",
      "transport_network_partition_blackbox",
      "data_integrity_corruption_blackbox",
      "upgrade_compatibility_persistence_blackbox",
      "telemetry_metrics_regression_blackbox",
    ]),
  );
  await expect(page.getByRole("button", { name: "从模板库导入" })).toBeVisible();
  await expect(page.getByRole("button", { name: "刷新模板库" })).toBeVisible();
  await expect(page.getByText("保存后才会出现在运行驾驶舱")).toBeVisible();
  await expect(page.getByText("codehub-mcp").first()).toBeVisible();
  await expect(page.getByLabel("Workflow builder provider preset")).toBeVisible();
  await expect(page.getByLabel("Workflow builder MCP compatibility")).toContainText("CodeTalk 预取后注入");
  await expect(page.getByLabel("Workflow builder skills")).toBeVisible();
  await expect(page.getByLabel("Workflow builder skill search")).toBeVisible();
  await expect(page.getByText(/已选\s+\d+/)).toBeVisible();
  await expect(page.getByLabel("Workflow builder visible skill count")).toContainText(/\d+\/\d+/);
  await expect(page.getByLabel("Workflow builder skill sfmea")).toBeChecked();
  await page.getByLabel("New workflow input name").fill("iSCSI 登录脚本");
  await page.getByLabel("New workflow input id").fill("iscsi_login_script");
  await page.getByLabel("New workflow input type").selectOption("file");
  await page.getByRole("button", { name: "添加输入契约" }).click();
  await expect(page.getByText(/iSCSI 登录脚本\s+iscsi_login_script/)).toBeVisible();
  await page.getByLabel("New workflow input name").fill("变更 MR");
  await page.getByLabel("New workflow input id").fill("change_mr");
  await page.getByLabel("New workflow input type").selectOption("mr_link");
  await page.getByLabel("New workflow input resolver").selectOption("agent_mcp");
  await page.getByRole("button", { name: "添加输入契约" }).click();
  await expect(page.getByText(/变更 MR\s+change_mr:mr_link/)).toBeVisible();
  await page.getByRole("button", { name: "保存工作流" }).click();
  await expect(page.getByText(/工作流已保存:/)).toBeVisible();
  await openWorkbenchView(page, "运行驾驶舱");
  await expect(page.getByLabel("Workspace selector")).toBeVisible();
  await expect(page.getByLabel("Repo path")).toHaveCount(0);
  await page.getByLabel("Workspace selector").selectOption("ws_spdk");
  await expect(page.getByLabel("Workspace selector")).toHaveValue("ws_spdk");
  await expect(page.getByText("源码路径: /Volumes/Media/dpdk/spdk")).toBeVisible();
  await expect(page.getByLabel("Workflow input iscsi_login_script")).toBeVisible();
  await expect(page.getByLabel("Workflow input change_mr")).toBeVisible();
  await openWorkbenchView(page, "工作流设计");
  await page.locator(".ct-workflow-node").first().click({ position: { x: 44, y: 18 } });
  await expect(page.getByLabel("Workflow inspector")).toBeVisible();
  await page.getByText("输出契约", { exact: true }).scrollIntoViewIfNeeded();
  const newOutputName = page.getByLabel("New workflow output name");
  const newOutputId = page.getByLabel("New workflow output id");
  const newOutputArtifact = page.getByLabel("New workflow output artifact");
  await expect(newOutputId).toBeVisible();
  await newOutputName.click();
  await newOutputName.pressSequentially("登录 SFMEA 表");
  await page.getByLabel("New workflow output type").selectOption("json");
  await newOutputArtifact.click();
  await newOutputArtifact.pressSequentially("login_sfmea.json");
  await newOutputId.click();
  await newOutputId.pressSequentially("login_sfmea");
  await expect(newOutputId).toHaveValue("login_sfmea");
  await expect(newOutputArtifact).toHaveValue("login_sfmea.json");
  await page.getByRole("button", { name: "添加输出契约" }).click();
  await expect(page.getByText(/登录 SFMEA 表\s+login_sfmea\.json/)).toBeVisible();
  await page.getByLabel("Workflow builder provider preset").selectOption("corp-agent");
  await expect(
    page.getByRole("textbox", { name: "Workflow builder provider" }),
  ).toHaveValue("corp-agent");
  await page.getByLabel("Workflow builder MCP 配置").selectOption("codehub-readonly");
  await expect(page.getByLabel("Workflow builder MCP compatibility")).toContainText("Agent 可直接使用 MCP");
  await page.getByRole("button", { name: "生成草稿" }).click();
  await expect(page.getByText("高级 Workflow JSON")).toBeVisible();
  await expect(page.getByLabel("Workflow JSON")).not.toBeVisible();
  await page.getByText("高级 Workflow JSON").click();
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"id": "iscsi_login_script"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"label": "iSCSI 登录脚本"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"id": "change_mr"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"resolver": "agent_mcp"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"id": "login_sfmea"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"label": "登录 SFMEA 表"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"edges": \[/);
  const generatedWorkflow = JSON.parse(
    await page.getByLabel("Workflow JSON").inputValue(),
  ) as {
    steps?: Array<Record<string, unknown>>;
    outputs?: Array<Record<string, unknown>>;
    ui?: { layout?: { nodes?: Array<Record<string, unknown>> } };
  };
  const jsonOutputsWithoutSchema = (generatedWorkflow.outputs ?? [])
    .filter((output) => output.type === "json" && typeof output.schema !== "object")
    .map((output) => output.id);
  expect(jsonOutputsWithoutSchema).toEqual([]);
  const canvasAgentNode = (generatedWorkflow.ui?.layout?.nodes ?? []).find(
    (node) => String(node.title ?? "") === "自定义智能体节点",
  );
  expect(canvasAgentNode).toBeTruthy();
  expect(canvasAgentNode?.config).toEqual(
    expect.objectContaining({
      provider: "claude-code",
      mcp_profile: "codehub-mcp",
    }),
  );
  const canvasAgentId = String(
    (canvasAgentNode?.config as Record<string, unknown> | undefined)?.id ?? "",
  );
  expect(canvasAgentId).toMatch(/^canvas_agent_/);
  const canvasAgentStep = generatedWorkflow.steps?.find(
    (step) => step.id === canvasAgentId,
  );
  expect(canvasAgentStep).toEqual(
    expect.objectContaining({
      id: canvasAgentId,
      type: "agent_task",
      provider: "claude-code",
      mcp_profile: "codehub-mcp",
    }),
  );
  expect(canvasAgentStep?.required_artifacts).toEqual(
    expect.arrayContaining(["login_sfmea.json"]),
  );
  expect(
    generatedWorkflow.outputs?.find((output) => output.id === "login_sfmea"),
  ).toEqual(
    expect.objectContaining({
      from: canvasAgentId,
      artifact: "login_sfmea.json",
    }),
  );
  await expect(page.getByLabel("Workflow builder evidence mappings")).toHaveValue(
    /"patch_impact_scope"/,
  );
  await expect(page.getByLabel("Workflow builder semantic imports")).toHaveValue(
    /"black_box_cases"/,
  );
  await page.getByLabel("Workflow builder scenario").selectOption("resource_leak_hunt");
  await page.getByRole("button", { name: "生成草稿" }).click();
  await expect(page.getByText("工作流草稿已生成: custom_mr_blackbox")).toBeVisible();
  await expect(page.getByText("Draft:ready")).toBeVisible();
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"test_hooks"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"ui": \{/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"layout": \{/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"nodes": \[/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"schema": \{\s+"type": "array"/);
  await page.getByLabel("Workflow builder scenario").selectOption("testing_activity_orchestration");
  await page.getByRole("button", { name: "生成草稿" }).click();
  await expect(page.getByText("工作流草稿已生成: custom_mr_blackbox")).toBeVisible();
  await expect(page.getByText("Draft:ready")).toBeVisible();
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"test_goal"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"test_plan"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"execution_matrix"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"coverage_gap_report"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"test-strategy-planning"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"test-execution-orchestration"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"performance-reliability-testing"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"artifact": "test_plan\.json"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"required": \[\s+"scope",\s+"risks",\s+"activities",\s+"entry_criteria",\s+"exit_criteria"\s+\]/);
  await page.getByLabel("Workflow builder scenario").selectOption("source_flow_sfmea_blackbox");
  await page.getByRole("button", { name: "生成草稿" }).click();
  await expect(page.getByText("工作流草稿已生成: custom_mr_blackbox")).toBeVisible();
  await expect(page.getByText("Draft:ready")).toBeVisible();
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/GitNexus 和 CGC/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"sfmea"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"black_box_cases"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"artifact": "sfmea\.json"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"schema": \{\s+"type": "array"/);
  await expect(page.getByRole("button", { name: "审计草稿" })).toBeEnabled();
  await page.getByRole("button", { name: "审计草稿" }).click();
  await expect(page.getByText("工作流草稿审计: ok (0 warning(s))")).toBeVisible();
  await page.getByLabel("Workflow builder scenario").selectOption("patch_impact_review");
  await page.getByRole("button", { name: "生成草稿" }).click();
  await expect(page.getByText("工作流草稿已生成: custom_mr_blackbox")).toBeVisible();
  await expect(page.getByText("Draft:ready")).toBeVisible();
  await expect(page.getByText("输出契约预览")).toBeVisible();
  await expect(page.getByText(/test_cases:test_cases/)).toBeVisible();
  await expect(page.getByText("semantic_import", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"patch_diff"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"provider": "corp-agent"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"mcp_profile": "codehub-readonly"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"skills": \[/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"sfmea"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"skill_instructions": \[/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"prompt_hint": "SFMEA/);
  await expect(page.getByLabel("Workflow builder input schemas")).toHaveValue(/"patch_diff"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"schema": \{\s+"type": "object"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"required": \[\s+"path"\s+\]/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"before_after_flow"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"validate_evidence"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"evidence_memory"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"semantic_import"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"kind": "patch_impact_scope"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"path_field": "file_path"/);
  await page.getByRole("button", { name: "保存工作流" }).click();
  await expect(page.getByText(/工作流已保存:/)).toBeVisible();
  await openWorkbenchView(page, "运行驾驶舱");
  await expect(page.getByText("工作流输入")).toBeVisible();
  const runConstraints = page.getByLabel("Run constraints");
  await expect(runConstraints.getByText("运行约束")).toBeVisible();
  await expect(runConstraints.getByText("MCP: codehub-readonly")).toBeVisible();
  await expect(runConstraints.getByText("SFMEA")).toBeVisible();
  await expect(runConstraints.getByText("黑盒测试设计")).toBeVisible();
  await expect(page.getByText(/test_cases -> black_box_cases\.json/)).toBeVisible();
  await page.getByLabel("Workflow input patch_diff").fill("E:/patches/tls.patch");
  await page.getByLabel("Upload file for patch_diff").setInputFiles({
    name: "tls.patch",
    mimeType: "text/x-patch",
    buffer: Buffer.from("diff --git a/tls.c b/tls.c\n"),
  });
  await expect(page.getByText("Input file uploaded: tls.patch")).toBeVisible();
  await page.getByLabel("Workflow input design_doc").fill("E:/docs/tls-design.md");
  await page.getByLabel("Workflow input analysis_object").fill("nvme-tcp-tls");
  await expect(page.getByText("高级输入 JSON")).toBeVisible();
  await expect(page.getByLabel("Inputs JSON")).not.toBeVisible();
  await page.getByText("高级输入 JSON").click();
  await expect(page.getByLabel("Inputs JSON")).toHaveValue(/"patch_diff": \{\s+"path": "input_uploads\/input_patch_upload\/tls\.patch"\s+\}/);
  await expect(page.getByLabel("Inputs JSON")).toHaveValue(/"design_doc": \{\s+"path": "E:\/docs\/tls-design\.md"\s+\}/);
  await expect(page.getByLabel("Inputs JSON")).toHaveValue(/"analysis_object": "nvme-tcp-tls"/);
  await expect(page.getByRole("button", { name: "准备运行" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "执行工作流" })).toBeDisabled();
  await expect(page.getByLabel("Workspace selector")).toBeVisible();
  await expect(page.getByLabel("Repo path")).toHaveCount(0);
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
            timeout_sec: 900,
            idle_timeout_sec: 900,
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
            timeout_sec: 900,
            idle_timeout_sec: 900,
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
  await openWorkbenchView(page, "运行驾驶舱");
  const preparePanel = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "任务运行" }) });
  await expect(preparePanel.getByText("测试活动运行摘要")).toBeVisible();
  await expect(preparePanel.getByText("输出预期")).toBeVisible();
  await expect(preparePanel.getByText("运行前检查")).toBeVisible();
  await preparePanel.getByLabel("Workspace selector").selectOption("ws_spdk");
  await expect(preparePanel.getByText("源码路径: /Volumes/Media/dpdk/spdk")).toBeVisible();
  await expect(preparePanel.getByText("工作空间: ws_spdk")).toBeVisible();
  await expect(preparePanel.getByRole("button", { name: "准备运行" })).toBeEnabled();
  await preparePanel.getByRole("button", { name: "准备运行" }).click();
  const diagnosticsDetails = page.getByLabel("运行详细诊断");
  await expect(diagnosticsDetails).toBeVisible();
  const resultPanel = page.getByLabel("运行结果面板");
  await expect(resultPanel).toBeVisible();
  const capabilityPanel = resultPanel.getByLabel("能力就绪面板");
  await expect(capabilityPanel).toBeVisible();
  await expect(capabilityPanel.getByText("降级可用")).toBeVisible();
  await expect(capabilityPanel.getByText("GitNexus", { exact: true })).toBeVisible();
  await expect(capabilityPanel.getByText("Agent · Claude Code")).toBeVisible();
  await expect(capabilityPanel.getByText("Claude Code 执行器不可用")).toBeVisible();
  await expect(preparePanel.getByText("Agent 运行阶段")).toBeHidden();
  await diagnosticsDetails.getByText("查看详细诊断与原始产物").click();
  await expect(preparePanel.getByText("Agent 运行阶段")).toBeVisible();
  await expect(preparePanel.getByText("准备上下文").first()).toBeVisible();
  await expect(preparePanel.getByText("执行 Agent").first()).toBeVisible();
  await expect(preparePanel.getByText("校验证据").first()).toBeVisible();
  await expect(preparePanel.getByText("固化交付物").first()).toBeVisible();
  await expect(preparePanel.getByText("可信度与可用性")).toBeVisible();
  await expect(preparePanel.getByText("交付物状态")).toBeVisible();
  await expect(resultPanel.getByText("运行状态")).toBeVisible();
  await expect(resultPanel.getByText("演示状态")).toHaveCount(0);
  await expect(resultPanel.getByText("进行中")).toBeVisible();
  await expect(resultPanel.getByText("准备上下文")).toBeVisible();
  await expect(resultPanel.getByText("产物与结果")).toBeVisible();
  await expect(
    preparePanel.getByRole("button", { name: /task_bundle\.json.*task_bundle/ }),
  ).toBeVisible();
  await expect(page.getByText("Input context: 1 files")).toBeVisible();
  await expect(page.getByText("tls-design.md")).toBeVisible();
  await expect(page.getByText("chunks:2")).toBeVisible();
  await expect(page.getByText("warnings:preview truncated")).toBeVisible();
  await expect(page.getByText("fast-context: fallback to agent_cli")).toBeVisible();
  await expect(page.getByText("执行器就绪度:")).toBeVisible();
  await expect(page.getByText(/执行器就绪度:\s*降级可用/)).toBeVisible();
  await expect(page.getByText("GitNexus：缺少配置")).toBeVisible();
  await expect(page.getByText("CGC：不可用")).toBeVisible();
  await expect(page.getByText("Claude Code：不可用")).toBeVisible();
  await expect(
    page.getByText("Claude Code 命令:ccr code 已使用备用命令", { exact: false }),
  ).toBeVisible();
  await expect(page.getByText("主执行器不可用，已尝试备用命令").first()).toBeVisible();
  await expect(
    page.getByText("探测:/api/tools/claude-code/startup-probe", { exact: false }),
  ).toBeVisible();
  await preparePanel.getByRole("button", { name: "验收审计" }).click();
  await expect(preparePanel.getByText("交付物状态")).toBeVisible();
  await expect(
    preparePanel.getByRole("button", { name: /task_bundle\.json.*task_bundle/ }),
  ).toBeVisible();
  await expect(
    preparePanel.getByRole("button", {
      name: /workflow_output_materialization\.json.*workflow_output_materialization/,
    }),
  ).toBeVisible();
  await expect(preparePanel.getByText(/缺少必需项:\s*2/).first()).toBeVisible();
  await expect(
    page.getByText("手动检查:POST /api/tools/claude-code/startup-probe", { exact: false }),
  ).toBeVisible();
  await expect(resultPanel.getByText("失败", { exact: true }).first()).toBeVisible();
  await expect(resultPanel.getByText("失败原因", { exact: true })).toBeVisible();
  await expect(resultPanel.getByText("缺少 2 个必需验收项").first()).toBeVisible();
  await expect(resultPanel.getByText("Agent 指令策略缺失")).toBeVisible();
  await expect(resultPanel.getByText("输入脱敏标记缺失")).toBeVisible();
  await expect(resultPanel.getByText("产物与结果")).toBeVisible();
  await expect(resultPanel.getByText("交付文件 1")).toBeVisible();
  await expect(resultPanel.getByText("内部诊断 8")).toBeVisible();
  await expect(page.getByText("gitnexus:missing_config")).toHaveCount(0);
  await expect(page.getByText("cgc:unavailable")).toHaveCount(0);
  await expect(page.getByText("claude-code:unavailable")).toHaveCount(0);
  await expect(resultPanel.getByText("missing-required")).toHaveCount(0);
  await expect(resultPanel.getByText("agent_instruction_policy_missing")).toHaveCount(0);
  await expect(resultPanel.getByText("stdin_redacted_flag_missing")).toHaveCount(0);
  await expect(preparePanel.getByText("reason:agent_instruction_policy_missing")).toHaveCount(0);
  await expect(preparePanel.getByText("reason:stdin_redacted_flag_missing")).toHaveCount(0);
  await resultPanel.getByText("内部诊断 8").click();
  await expect(
    page.getByRole("button", {
      name: /agent_failure_retry_context:agent_runs\/discover\/failure_retry_context\.json\s*已脱敏/,
    }),
  ).toBeVisible();

  const taskBundlePreviewButton = page.getByRole("button", {
    name: "task_bundle:task_bundle.json",
  });
  await expect(taskBundlePreviewButton).toBeEnabled();
  await taskBundlePreviewButton.click();

  await expect(page.getByText("sha:abc123abc123").first()).toBeVisible();
  await expect(page.getByText("\"provider\":\"claude-code\"", { exact: false }).first()).toBeVisible();
  await expect(resultPanel.getByRole("button", { name: "下载预览" })).toBeVisible();

  await page
    .getByRole("button", {
      name: "evidence_validation:steps/validate_evidence/evidence_validation.json",
    })
    .click();

  await expect(page.getByText("已接收产物: 2")).toBeVisible();
  await expect(page.getByText("被拒绝产物: 1")).toBeVisible();
  await expect(page.getByText("source_scope.json sha:111122223333")).toBeVisible();

  await page
    .getByRole("button", {
      name: "workflow_output_materialization:workflow_output_materialization.json",
    })
    .last()
    .click();
  await expect(page.getByText("已固化证据: 2")).toBeVisible();
  await expect(page.getByText("被拒绝输出: 1")).toBeVisible();
  await expect(page.getByText("声明输出: 3")).toBeVisible();
  await expect(page.getByText("首个拒绝项: bad")).toBeVisible();
  await expect(page.getByText("原因:output_not_ok")).toBeVisible();
  await expect(page.getByText("状态:无效")).toBeVisible();
  await expect(page.getByText("Schema 错误:1")).toBeVisible();
  await expect(page.getByText("工作流输出 sha:999988887777")).toBeVisible();
  await expect(
    page.getByText("workflow_output:task_run_preview/black_box_cases", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByText("changed_behavior:tls_handshake_retry", { exact: false }),
  ).toBeVisible();
  await expect(page.getByText("映射:changed_behavior", { exact: false })).toBeVisible();

  await page
    .getByRole("button", {
      name: "semantic_import_outputs:semantic_import_outputs_by_step.json",
    })
    .click();
  await expect(page.getByText("\"output_id\":\"black_box_cases\"", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("\"module\":\"nvmf_tcp_tls\"", { exact: false }).first()).toBeVisible();

  await page
    .getByRole("button", {
      name: "memory_retrieval:memory_retrieval.json",
    })
    .click();
  await expect(page.getByText("记忆检索")).toBeVisible();
  await expect(page.getByText(/^证据:1$/)).toBeVisible();
  await expect(page.getByText("部署证据:1").first()).toBeVisible();
  await expect(page.getByText("语义:1")).toBeVisible();
  await expect(page.getByText("源码片段:2")).toBeVisible();
  await expect(page.getByText("查询:nvme tcp tls")).toBeVisible();
  await expect(
    page.getByText("首项:nof/nvmf_tcp/transport/tls/tls.c"),
  ).toBeVisible();
  await expect(page.getByText("复用原因:source slices attached and locally verified")).toBeVisible();

  await page
    .getByRole("button", {
      name: "input_materials:input_materials.json",
    })
    .click();
  await expect(page.getByText("输入材料", { exact: true })).toBeVisible();
  await expect(page.getByText("材料数:1")).toBeVisible();
  await expect(page.getByText("必读:true")).toBeVisible();
  await expect(page.getByText("源码真相:false")).toBeVisible();
  await expect(page.getByText("阅读顺序:design_doc")).toBeVisible();
  await expect(page.getByText("首项:design_doc")).toBeVisible();
  await expect(page.getByText("角色:design context")).toBeVisible();
  await expect(page.getByText("文件:tls-design.md")).toBeVisible();
  await expect(page.getByText("sha:1234567890ab")).toBeVisible();

  await page.getByText("支撑文件 2").last().click();
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
  await expect(page.getByText("回放状态: 已就绪")).toBeVisible();
  await expect(page.getByText("执行器:claude-code")).toBeVisible();
  await expect(page.getByText("提示词:execution_input.json:stdin")).toBeVisible();
  await expect(page.getByText("只读:true")).toBeVisible();
  await expect(page.getByText("哈希:3")).toBeVisible();
  await expect(page.getByText("任务包 sha:taskhash1234")).toBeVisible();

  await page
    .getByRole("button", {
      name: "agent_execution_input:agent_runs/discover/execution_input.json",
    })
    .click();
  await expect(page.getByText("执行输入")).toBeVisible();
  await expect(page.getByText("执行器:claude-code")).toBeVisible();
  await expect(page.getByText("传输:stdin")).toBeVisible();
  await expect(page.getByText("原因:transport_fallback_from_argv")).toBeVisible();
  await expect(page.getByText("标准输入已脱敏:true")).toBeVisible();
  await expect(page.getByText("stdin sha:stdinsha1234")).toBeVisible();
  await expect(page.getByText("契约 sha:contracthash")).toBeVisible();

  await page
    .getByRole("button", {
      name: "agent_failure_retry_context:agent_runs/discover/failure_retry_context.json",
    })
    .click();
  await expect(page.getByText("失败重试")).toBeVisible();
  await expect(page.getByText("节点:discover")).toBeVisible();
  await expect(page.getByText("类型:agent_error")).toBeVisible();
  await expect(page.getByText("可重试:true")).toBeVisible();
  await expect(page.getByText("退出码:7")).toBeVisible();
  await expect(page.getByText("缺失产物:source_scope.json")).toBeVisible();
  await expect(page.getByText("必须生成:source_scope.json")).toBeVisible();
  await expect(
    page.getByText("避免重复:do not treat raw stdout/stderr as accepted evidence"),
  ).toBeVisible();
  await expect(page.getByText("已脱敏", { exact: true }).first()).toBeVisible();
  await expect(resultPanel.getByRole("button", { name: "下载脱敏预览" })).toBeVisible();
  await expect(page.locator("body")).not.toContainText(redactedArtifactSecret);

  await page.getByRole("button", { name: "验收审计" }).click();
  await expect(page.getByText("Agent 指令策略", { exact: true })).toBeVisible();
  await expect(page.getByText("原因:Agent 指令策略缺失")).toBeVisible();
  await expect(page.getByText("期望文件:AGENTS.md")).toBeVisible();
  await expect(page.getByText("Agent 输入脱敏")).toBeVisible();
  await expect(page.getByText("原因:输入脱敏标记缺失")).toBeVisible();
  await expect(page.getByText("agent_turn_instruction_policy")).toHaveCount(0);
  await expect(page.getByText("agent_turn_stdin_redaction")).toHaveCount(0);
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
  await preparePanel.getByLabel("Workspace selector").selectOption("ws_spdk");
  await preparePanel.getByRole("button", { name: "准备运行" }).hover();
  await preparePanel.getByRole("button", { name: "准备运行" }).click();
  await expect(page.getByText("运行产物", { exact: true }).first()).toBeVisible();
  await page.getByLabel("运行详细诊断").getByText("查看详细诊断与原始产物").click();
  await expect(page.getByText("交付文件: 0 · 输入材料: 0 · 内部诊断: 1")).toBeVisible();
  await page.getByText("内部诊断 1").last().click();

  const previewButton = page.getByRole("button", {
    name: "task_bundle:task_bundle.json",
  });
  await expect(previewButton).toBeEnabled();
  await previewButton.hover();
  await previewButton.dblclick();

  await expect(page.getByText("sha:abc123abc123")).toBeVisible();
  await expect(page.getByText("\"double_click_safe\":true", { exact: false })).toBeVisible();
  await expect.poll(() => contentRequests).toBe(1);
});

test("agent workbench opens one AI review thread on double click", async ({ page }) => {
  await routeWorkbenchShell(page);
  let createConversationCalls = 0;
  let createConversationBody: Record<string, unknown> | null = null;

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
  await page.route("**/api/workbench/task-runs/task_run_ai_review/execute", async (route) => {
    await route.fulfill({
      headers: corsHeaders(route.request().headers().origin),
      json: {
        task_run_id: "task_run_ai_review",
        workflow_id: "mr-blackbox-workflow",
        status: "completed",
        started_at: "2026-06-23T00:01:00Z",
        completed_at: "2026-06-23T00:02:30Z",
        step_results: [
          {
            step_id: "collect",
            step_label: "收集证据",
            status: "ok",
            duration_ms: 1200,
          },
          {
            step_id: "test_design",
            step_label: "生成测试设计",
            status: "ok",
            duration_ms: 3600,
          },
        ],
        outputs: [
          {
            id: "sfmea",
            status: "ok",
            artifact: "sfmea.json",
          },
          {
            id: "black_box_cases",
            status: "ok",
            artifact: "black_box_cases.json",
          },
        ],
        audit_summary: {
          completed_steps: 2,
          failed_steps: 0,
          failure_kinds: [],
        },
        test_activity_quality: {
          status: "deliverable",
          score: 88,
          issue_count: 1,
          recommendations: ["补充 iSCSI CHAP 异常恢复观测点"],
        },
      },
    });
  });
  await page.route(
    "**/api/workbench/task-runs/task_run_ai_review/rerun-plan/validate",
    async (route) => {
      await route.fulfill({
        headers: corsHeaders(route.request().headers().origin),
        json: { can_rerun: false, blockers: [], warnings: [] },
      });
    },
  );
  await page.route("**/api/workbench/task-runs/task_run_ai_review/artifacts", async (route) => {
    await route.fulfill({
      headers: corsHeaders(route.request().headers().origin),
      json: {
        task_run_id: "task_run_ai_review",
        artifact_dir: "E:/data/workbench/task_runs/task_run_ai_review",
        artifacts: [
          {
            task_run_id: "task_run_ai_review",
            artifact_dir: "E:/data/workbench/task_runs/task_run_ai_review",
            relative_path: "sfmea.json",
            kind: "workflow_output",
            size_bytes: 1024,
            sha256: "sfmea-sha",
            audience: "deliverable",
            is_text: true,
            truncated: false,
          },
          {
            task_run_id: "task_run_ai_review",
            artifact_dir: "E:/data/workbench/task_runs/task_run_ai_review",
            relative_path: "black_box_cases.json",
            kind: "workflow_output",
            size_bytes: 2048,
            sha256: "cases-sha",
            audience: "deliverable",
            is_text: true,
            truncated: false,
          },
        ],
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
    createConversationBody = await route.request().postDataJSON();
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
  await page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "任务运行" }) })
    .getByLabel("Workspace selector")
    .selectOption("ws_spdk");
  await page.getByRole("button", { name: "准备运行" }).click();
  await page.getByLabel("运行详细诊断").getByText("查看详细诊断与原始产物").click();
  await expect(page.getByRole("paragraph").filter({ hasText: /^task_run_ai_review$/ })).toBeVisible();
  await page.getByRole("button", { name: "执行工作流" }).click();
  await expect(page.getByText("运行完成", { exact: false }).first()).toBeVisible();

  await page.getByRole("button", { name: "围绕本次运行继续追问" }).dblclick();

  await expect.poll(() => createConversationCalls).toBe(1);
  expect(createConversationBody?.initial_context).toMatchObject({
    workflow_id: "mr-blackbox-workflow",
    task_run_id: "task_run_ai_review",
    workflow_execution_summary: {
      status: "completed",
      completed_steps: 2,
      failed_steps: 0,
      output_count: 2,
      failure_kinds: [],
    },
    test_activity_quality: {
      status: "deliverable",
      score: 88,
      issue_count: 1,
      recommendations: ["补充 iSCSI CHAP 异常恢复观测点"],
    },
    deliverables: [
      { id: "sfmea", status: "ok", artifact: "sfmea.json" },
      { id: "black_box_cases", status: "ok", artifact: "black_box_cases.json" },
    ],
    artifact_manifest_summary: {
      artifact_count: 2,
      user_deliverable_count: 2,
      diagnostic_count: 0,
    },
  });
});

test("recent task runs stay bounded when history is large", async ({ page }) => {
  await routeWorkbenchShell(page);
  const runs = Array.from({ length: 28 }, (_, index) => ({
    task_run_id: `task_run_history_${index}`,
    workflow_id: `history_workflow_${index}`,
    workspace_id: "ws_spdk",
    repo_path: "/Volumes/Media/dpdk/spdk",
    artifact_dir: `/tmp/task_run_history_${index}`,
    workflow_snapshot: {},
    input_snapshot: {},
    task_bundle: {},
    agent_runs: [],
    created_at: `2026-07-05T00:${String(index).padStart(2, "0")}:00Z`,
  }));
  await page.route("**/api/workbench/task-runs**", async (route) => {
    await route.fulfill({
      json: { items: runs },
      headers: corsHeaders(route.request().headers().origin),
    });
  });

  await gotoWorkbench(page);
  await openWorkbenchView(page, "运行驾驶舱");

  await expect(page.getByLabel("最近任务运行", { exact: true })).toBeVisible();
  const recentList = page.getByLabel("最近任务运行列表");
  await expect(recentList).toBeVisible();
  const recentListBox = await recentList.boundingBox();
  expect(recentListBox?.height ?? 9999).toBeLessThanOrEqual(340);
});
