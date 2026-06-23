import { expect, test } from "@playwright/test";

function corsHeaders(origin = "http://localhost:3005") {
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
        path: "E:/data/workbench/input_uploads/input_patch_upload/tls.patch",
        input_payload: {
          path: "E:/data/workbench/input_uploads/input_patch_upload/tls.patch",
        },
      },
      headers: corsHeaders(route.request().headers().origin),
    });
  });
}

test("agent workbench renders workflow and task-run controls", async ({ page }) => {
  test.setTimeout(60_000);
  await routeWorkbenchShell(page);

  await page.goto("/workbench", { waitUntil: "domcontentloaded", timeout: 60_000 });

  await expect(page.getByRole("heading", { name: "Agent Workbench" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Provider Matrix" })).toBeVisible();
  await expect(page.getByText("ccr code", { exact: true }).first()).toBeVisible();
  await expect(
    page.getByText("/api/tools/claude-code/startup-probe", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("claude_print_arg").first()).toBeVisible();
  await expect(page.getByText("Resolution: available")).toBeVisible();
  await expect(page.getByText("fallback").first()).toBeVisible();
  await expect(page.getByText("launch:exec")).toBeVisible();
  await expect(
    page.getByText("Reason: primary command unavailable; using fallback: claude"),
  ).toBeVisible();
  await expect(page.getByText("Probe recipe")).toBeVisible();
  await expect(page.getByText("Backend command: ccr code")).toBeVisible();
  await expect(page.getByText("Override env: CLAUDE_CODE_COMMAND")).toBeVisible();
  await expect(page.getByText("Check: PATH, CCR_CONFIG_PATH, CLAUDE_CODE_CONFIG_PATH")).toBeVisible();
  await expect(page.getByText(/PowerShell profile/)).toBeVisible();
  await expect(page.getByText("Agent-owned").first()).toBeVisible();
  await expect(page.getByText("CodeTalk callable").first()).toBeVisible();
  await expect(page.getByText("Local repo search")).toBeVisible();
  await expect(page.getByText("source discovery")).toBeVisible();
  await expect(page.getByText("source slices")).toBeVisible();
  await expect(page.getByText("fast-context").first()).toBeVisible();
  await expect(page.getByText("codetalk_mcp_bridge")).toBeVisible();
  await page.getByRole("button", { name: "Startup probe" }).click();
  await expect(page.getByText("Startup probe ok: claude-code")).toBeVisible();
  await expect(page.getByText("startup_probe_ok via ccr code")).toBeVisible();
  await expect(page.getByText("Probe launch: powershell-profile")).toBeVisible();
  await expect(page.getByText("Probe attempts: 1")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Workflow Registry" })).toBeVisible();
  await expect(page.getByLabel("Workflow builder scenario")).toBeVisible();
  await expect(page.getByRole("button", { name: "Apply preset" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Install preset" })).toBeVisible();
  await expect(page.getByText("codehub-mcp")).toBeVisible();
  await page.getByLabel("Workflow builder scenario").selectOption("patch_impact");
  await page.getByRole("button", { name: "Generate draft" }).click();
  await expect(page.getByText("Workflow draft generated: custom_mr_blackbox")).toBeVisible();
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"patch_file"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"before_after_flow"/);
  await expect(page.getByLabel("Workflow JSON")).toHaveValue(/"render_report"/);
  await expect(page.getByText("Workflow inputs")).toBeVisible();
  await page.getByLabel("Workflow input patch_file").fill("E:/patches/tls.patch");
  await page.getByLabel("Upload file for patch_file").setInputFiles({
    name: "tls.patch",
    mimeType: "text/x-patch",
    buffer: Buffer.from("diff --git a/tls.c b/tls.c\n"),
  });
  await expect(page.getByText("Input file uploaded: tls.patch")).toBeVisible();
  await page.getByLabel("Workflow input design_doc").fill("E:/docs/tls-design.md");
  await page.getByLabel("Workflow input analysis_object").fill("nvme-tcp-tls");
  await expect(page.getByLabel("Inputs JSON")).toHaveValue(/"patch_file": \{\s+"path": "E:\/data\/workbench\/input_uploads\/input_patch_upload\/tls\.patch"\s+\}/);
  await expect(page.getByLabel("Inputs JSON")).toHaveValue(/"design_doc": \{\s+"path": "E:\/docs\/tls-design\.md"\s+\}/);
  await expect(page.getByLabel("Inputs JSON")).toHaveValue(/"analysis_object": "nvme-tcp-tls"/);
  await expect(page.getByRole("button", { name: "Prepare run" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Execute workflow" })).toBeDisabled();
  await expect(page.getByLabel("Repo path")).toBeVisible();
});

test("agent workbench searches semantic cases and evidence memory", async ({ page }) => {
  await routeWorkbenchShell(page);
  await page.route("**:8100/api/workbench/semantic-cases/search*", async (route) => {
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
  await page.route("**:8100/api/workbench/memory/search*", async (route) => {
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
  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { name: "Evidence Memory" })).toBeVisible();
  await page.getByLabel("Semantic feature").fill("NVMe TCP TLS");
  await page.getByLabel("Semantic module").fill("nvmf_tcp");
  await page
    .getByLabel("Semantic case lines")
    .fill("TLS key rotation fails -> old session remains connected until retry");
  await page.getByRole("button", { name: "Build semantic JSON" }).click();
  await expect(page.getByText("Semantic import draft generated: 1 cases")).toBeVisible();
  await expect(page.getByLabel("Semantic JSON")).toHaveValue(/"case_id": "nvmf_tcp_tls_key_rotation_fails_1"/);
  await expect(page.getByLabel("Semantic JSON")).toHaveValue(/"old session remains connected until retry"/);
  await expect(page.getByLabel("Semantic JSON")).toHaveValue(/"source_ref": "workbench_semantic_text_import"/);

  await Promise.all([
    page.waitForResponse((response) =>
      response.url().includes("/api/workbench/semantic-cases/search") &&
      response.status() === 200,
    ),
    page.getByRole("button", { name: "Search", exact: true }).click(),
  ]);
  await expect(page.getByText("Semantic results: 1")).toBeVisible();
  await expect(
    page.getByText("TLS handshake fails and connection is released", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Memory facts are structured evidence only")).toBeVisible();
  await Promise.all([
    page.waitForResponse((response) =>
      response.url().includes("/api/workbench/memory/search") &&
      response.status() === 200,
    ),
    page.getByRole("button", { name: "Search memory" }).click(),
  ]);
  await expect(page.getByText("Memory results: 1")).toBeVisible();
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

  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
  await expect(page.getByText("ccr code", { exact: true }).first()).toBeVisible();
  const preparePanel = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "Prepare Task Run" }) });
  const repoInput = preparePanel.getByLabel("Repo path");
  await repoInput.click();
  await repoInput.pressSequentially("E:/repo");
  await expect(repoInput).toHaveValue("E:/repo");
  await expect(preparePanel.getByRole("button", { name: "Prepare run" })).toBeEnabled();
  await preparePanel.getByRole("button", { name: "Prepare run" }).click();
  await expect(page.getByText("Input context: 1 files")).toBeVisible();
  await expect(page.getByText("tls-design.md")).toBeVisible();
  await expect(page.getByText("chunks:2")).toBeVisible();
  await expect(page.getByText("warnings:preview truncated")).toBeVisible();
  await expect(page.getByText("fast-context: fallback to agent_cli")).toBeVisible();
  await expect(page.getByText("Audit artifacts: 5")).toBeVisible();

  await page.getByRole("button", { name: "task_bundle:task_bundle.json" }).click();

  await expect(page.getByText("sha:abc123abc123")).toBeVisible();
  await expect(page.getByText("\"provider\":\"claude-code\"", { exact: false })).toBeVisible();

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

  await page
    .getByRole("button", {
      name: "semantic_import_outputs:semantic_import_outputs_by_step.json",
    })
    .click();
  await expect(page.getByText("\"output_id\":\"black_box_cases\"", { exact: false })).toBeVisible();
  await expect(page.getByText("\"module\":\"nvmf_tcp_tls\"", { exact: false })).toBeVisible();
});
