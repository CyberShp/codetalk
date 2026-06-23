import { expect, test } from "@playwright/test";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "8100"}`;

test("tools page renders readable management copy", async ({ page }) => {
  await page.route(`${backendBase}/api/tools/procs`, async (route) => {
    await route.fulfill({
      json: [
        {
          name: "claude-code",
          display_name: "Claude Code",
          healthy: true,
          status: "available",
          managed: false,
          message: "claude-code available",
          capabilities: ["code_search"],
          agent_provider: {
            provider: "claude-code",
            supports_mcp: true,
            mcp_profiles: ["codehub-mcp"],
            supports_artifact_export: true,
            supports_json_output: true,
            prompt_transport: "stdin",
            env_hint_keys: ["CORP_AGENT_PROFILE"],
          },
          agent_provider_diagnostics: {
            configured_command_text: "ccr code",
            env_hint_keys: ["CORP_AGENT_PROFILE"],
            env_hints: {
              CORP_AGENT_PROFILE: "innernet",
            },
          },
        },
      ],
    });
  });

  await page.goto("/tools", { waitUntil: "domcontentloaded" });

  await expect(page.getByRole("heading", { name: "工具状态" })).toBeVisible();
  await expect(page.getByText("查看和管理分析工具进程")).toBeVisible();
  await expect(page.getByText("env:CORP_AGENT_PROFILE")).toBeVisible();
  await expect(page.getByText("CORP_AGENT_PROFILE=innernet")).toBeVisible();
  await expect(page.getByRole("button", { name: "刷新" })).toBeVisible();
});

test("external agent tool card can run a startup probe", async ({ page }) => {
  test.setTimeout(60_000);

  await page.route(`${backendBase}/api/tools/procs`, async (route) => {
    await route.fulfill({
      json: [
        {
          name: "claude-code",
          display_name: "Claude Code",
          healthy: false,
          status: "unavailable",
          managed: false,
          last_check:
            "primary command unavailable; using fallback: claude -p --output-format json",
          message:
            "ccr code -p --output-format json => unavailable; claude -p --output-format json => unavailable",
          capabilities: ["code_search"],
        },
      ],
    });
  });

  await page.route(`${backendBase}/api/tools/claude-code/startup-probe`, async (route) => {
    expect(route.request().method()).toBe("POST");
    await route.fulfill({
      json: {
        provider: "claude-code",
        healthy: true,
        status: "ok",
        message:
          "startup_probe_ok; primary command unavailable; using fallback: claude -p --output-format json",
        health: {
          attempts: [
            {
              command: "ccr code -p --output-format json",
              status: "available",
              launch_kind: "exec",
              probe_status: "error",
              probe_message:
                "external agent exited with exit code 1; stdout: Config file not found at C:\\Users\\me\\.claude-code-router\\config-router.json",
            },
            {
              command: "claude -p --output-format json",
              status: "available",
              launch_kind: "exec",
              probe_status: "timeout",
              probe_message: "startup probe timed out",
            },
          ],
        },
      },
    });
  });

  await page.goto("/tools", { waitUntil: "domcontentloaded" });

  await expect(page.getByRole("heading", { name: "Claude Code" })).toBeVisible();
  await expect(
    page.getByText(
      "primary command unavailable; using fallback: claude -p --output-format json",
    ),
  ).toBeVisible();
  await expect(page.getByText("Invalid Date")).toHaveCount(0);
  await page.getByRole("button", { name: "Startup probe" }).click();

  await expect(page.getByText("startup_probe_ok")).toBeVisible();
  await expect(
    page.getByText("ccr code -p --output-format json", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("claude -p --output-format json", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Config file not found")).toBeVisible();
  await expect(page.getByText("startup probe timed out")).toBeVisible();
});

test("managed GitNexus card shows repo index startup probe diagnostics", async ({ page }) => {
  test.setTimeout(60_000);

  await page.route(`${backendBase}/api/tools/procs`, async (route) => {
    await route.fulfill({
      json: [
        {
          name: "gitnexus",
          display_name: "GitNexus",
          healthy: false,
          status: "error",
          managed: true,
          message: "Health endpoint unreachable",
        },
      ],
    });
  });

  await page.route(`${backendBase}/api/tools/gitnexus/startup-probe`, async (route) => {
    expect(route.request().method()).toBe("POST");
    await route.fulfill({
      json: {
        tool: "gitnexus",
        healthy: true,
        status: "ok",
        started: false,
        message: "startup probe ok: existing service already reachable",
        diagnostics: {
          repo_index: {
            service_reachable: true,
            repo_indexed: false,
            indexed_repo_count: 3,
            tool_repo_path: "E:\\codetalk_test\\codetalks-Test\\codetalk",
            message: "GitNexus reachable but this repo is not indexed",
          },
        },
      },
    });
  });

  await page.goto("/tools", { waitUntil: "domcontentloaded" });

  await expect(page.getByRole("heading", { name: "GitNexus" })).toBeVisible();
  await page.getByRole("button", { name: "Startup probe" }).click();

  await expect(page.getByText("gitnexus", { exact: true })).toBeVisible();
  await expect(page.getByText("repo not indexed")).toBeVisible();
  await expect(
    page.getByText("GitNexus reachable but this repo is not indexed"),
  ).toBeVisible();
  await expect(page.getByText("indexed repos: 3")).toBeVisible();
});
