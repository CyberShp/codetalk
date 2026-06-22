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
            unavailable_behavior: "Workflow continues with diagnostics.",
          },
          {
            provider: "fast-context",
            display_name: "fast-context",
            owner: "codetalk_mcp_bridge",
            status: "bridge_disabled",
            non_blocking: true,
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
            unavailable_behavior: "CodeTalk records unavailable and continues.",
          },
        ],
        notes: ["Agent CLI providers may call their own MCP tools."],
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
  await expect(page.getByText("ccr code")).toBeVisible();
  await expect(page.getByText("fast-context").first()).toBeVisible();
  await expect(page.getByText("codetalk_mcp_bridge")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Workflow Registry" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Apply preset" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Install preset" })).toBeVisible();
  await expect(page.getByText("codehub-mcp")).toBeVisible();
  await expect(page.getByRole("button", { name: "Prepare run" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Execute workflow" })).toBeDisabled();
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
  await page.goto("/workbench", { waitUntil: "domcontentloaded" });

  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByText("TLS handshake fails and connection is released")).toBeVisible();
  await expect(page.getByText("Memory facts are structured evidence only")).toBeVisible();
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

  await page.goto("/workbench", { waitUntil: "domcontentloaded" });
  await expect(page.getByText("ccr code")).toBeVisible();
  const preparePanel = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "Prepare Task Run" }) });
  const repoInput = preparePanel.getByLabel("Repo path");
  await repoInput.click();
  await repoInput.pressSequentially("E:/repo");
  await expect(repoInput).toHaveValue("E:/repo");
  await expect(preparePanel.getByRole("button", { name: "Prepare run" })).toBeEnabled();
  await preparePanel.getByRole("button", { name: "Prepare run" }).click();
  await expect(page.getByText("fast-context: fallback to agent_cli")).toBeVisible();
  await expect(page.getByText("Audit artifacts: 3")).toBeVisible();

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
});
