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
  await page.route("**/api/workbench/task-runs*", async (route) => {
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
