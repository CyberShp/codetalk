import { expect, test } from "@playwright/test";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "8100"}`;

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
              status: "unavailable",
              reason: "command not found: ccr",
            },
            {
              command: "claude -p --output-format json",
              status: "available",
              launch_kind: "exec",
            },
          ],
        },
      },
    });
  });

  await page.goto("/tools", { waitUntil: "domcontentloaded" });

  await expect(page.getByRole("heading", { name: "Claude Code" })).toBeVisible();
  await page.getByRole("button", { name: "Startup probe" }).click();

  await expect(page.getByText("startup_probe_ok")).toBeVisible();
  await expect(
    page.getByText("ccr code -p --output-format json", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("claude -p --output-format json", { exact: true }),
  ).toBeVisible();
});
