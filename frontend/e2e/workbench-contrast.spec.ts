import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

function corsHeaders(origin = "http://localhost:3003") {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Content-Type": "application/json",
  };
}

async function routeDiagnosticsWorkbench(page: Page) {
  await page.route("**/api/workbench/workflows", async (route) => {
    await route.fulfill({
      json: [
        {
          id: "mr-blackbox-workflow",
          name: "MR Black-box Test Workflow",
          version: 1,
          inputs: [
            { id: "mr_link", type: "mr_link", required: true },
            { id: "design_doc", type: "file", required: false },
          ],
          steps: [],
          outputs: [],
        },
      ],
      headers: corsHeaders(route.request().headers().origin),
    });
  });
  await page.route("**/api/workbench/task-runs**", async (route) => {
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
  await page.route("**/api/workbench/system-audit", async (route) => {
    await route.fulfill({
      json: {
        status: "ready",
        created_at: "2026-06-28T00:00:00Z",
        summary: {
          required_checks: 0,
          missing_required: 0,
          recommended_checks: 0,
          missing_recommended: 0,
        },
        checks: [],
        missing_required: [],
        missing_recommended: [],
        notes: [],
      },
      headers: corsHeaders(route.request().headers().origin),
    });
  });
  await page.route("**/api/workbench/workflow-capabilities", async (route) => {
    await route.fulfill({
      json: {
        status: "ok",
        input_types: ["file", "patch", "mr_link", "coverage_report"],
        input_resolvers: ["agent_mcp", "local"],
        step_types: ["agent_task", "evidence_validate"],
        output_types: ["json", "test_cases"],
        input_features: { json_schema_validation: true },
        output_features: { json_schema_validation: true },
        agent_cli_features: { agent_owned_mcp_credentials: true },
        semantic_library_import_formats: ["json", "jsonl"],
        artifact_contract: {},
      },
      headers: corsHeaders(route.request().headers().origin),
    });
  });
  await page.route("**/api/workbench/core-workflow-readiness", async (route) => {
    await route.fulfill({
      json: {
        status: "ready",
        summary: {
          workflow_count: 0,
          missing_required: 0,
          agent_step_count: 0,
          output_count: 0,
        },
        workflows: [],
        missing_required: [],
        notes: [],
      },
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
            fallback_commands: [],
            readonly_args: [],
            command_hint_env: "CLAUDE_CODE_COMMAND",
            capabilities: {
              provider: "claude-code",
              supports_mcp: false,
              mcp_profiles: [],
              supports_artifact_export: true,
              supports_json_output: true,
              prompt_transport: "claude_print_arg",
            },
            credential_boundary:
              "Agent CLI 自己持有 MCP 凭证和远端访问权限；CodeTalk 只下发任务包并校验返回产物。",
            diagnostics: {
              startup_probe_endpoint: "/api/tools/claude-code/startup-probe",
              startup_probe_transport: "claude_print_arg",
              command_resolution: {
                status: "unavailable",
                reason: "no agent command found; attempted: ccr code",
                attempt_count: 1,
              },
            },
            unavailable_behavior: "Workflow continues with diagnostics.",
          },
        ],
        notes: [],
      },
      headers: corsHeaders(route.request().headers().origin),
    });
  });
}

function parseRgb(color: string) {
  const match = color.match(/rgba?\(([^)]+)\)/);
  if (!match) return null;
  const [r, g, b, a = "1"] = match[1].split(",").map((part) => part.trim());
  return {
    r: Number(r),
    g: Number(g),
    b: Number(b),
    a: Number(a),
  };
}

test("run cockpit form labels are readable", async ({ page }) => {
  await page.goto("/workbench");
  await expect(page.getByRole("heading", { name: "任务运行" })).toBeVisible();

  const labels = ["工作流", "工作区 ID", "执行器覆盖"];
  for (const label of labels) {
    const color = await page
      .locator(".ct-workbench-panel label > span", { hasText: label })
      .first()
      .evaluate((element) => getComputedStyle(element).color);
    const rgb = parseRgb(color);

    expect(rgb, `${label} color should parse: ${color}`).not.toBeNull();
    expect(rgb!.a, `${label} should not be translucent`).toBeGreaterThanOrEqual(0.9);
    expect(rgb!.r, `${label} should be darker than muted helper text`).toBeLessThanOrEqual(90);
  }
});

test("workbench tabs and panels share one typography scale", async ({ page }) => {
  await page.goto("/workbench");
  await expect(page.getByRole("button", { name: /运行驾驶舱/ })).toBeVisible();

  const tabMetrics = await page.locator(".ct-workbench-tab").evaluateAll((tabs) =>
    tabs.map((tab) => {
      const title = tab.querySelector("span.truncate.text-sm");
      const description = tab.querySelector(":scope > span:last-child");
      const badge = tab.querySelector(":scope > span:first-child > span:last-child");
      return {
        titleSize: title ? getComputedStyle(title).fontSize : null,
        titleWeight: title ? getComputedStyle(title).fontWeight : null,
        descriptionSize: description ? getComputedStyle(description).fontSize : null,
        badgeSize: badge ? getComputedStyle(badge).fontSize : null,
      };
    }),
  );

  for (const metric of tabMetrics) {
    expect(metric.titleSize).toBe("14px");
    expect(Number(metric.titleWeight)).toBeGreaterThanOrEqual(600);
    expect(metric.descriptionSize).toBe("12px");
    expect(metric.badgeSize).toBe("11px");
  }

  await page.getByRole("button", { name: /工作流设计/ }).click();
  await expect(page.getByRole("heading", { name: "工作流编排" })).toBeVisible();

  const panelMetrics = await page.evaluate(() => {
    const panel = document.querySelector(".ct-workbench-panel");
    if (!panel) return null;

    const firstLabel = panel.querySelector("label > span");
    const firstSelect = panel.querySelector("select");
    const firstInput = panel.querySelector("input");
    const firstButton = panel.querySelector("button");
    return {
      labelSize: firstLabel ? getComputedStyle(firstLabel).fontSize : null,
      labelWeight: firstLabel ? getComputedStyle(firstLabel).fontWeight : null,
      selectSize: firstSelect ? getComputedStyle(firstSelect).fontSize : null,
      inputSize: firstInput ? getComputedStyle(firstInput).fontSize : null,
      buttonSize: firstButton ? getComputedStyle(firstButton).fontSize : null,
    };
  });

  expect(panelMetrics).not.toBeNull();
  expect(panelMetrics!.labelSize).toBe("12px");
  expect(Number(panelMetrics!.labelWeight)).toBeGreaterThanOrEqual(580);
  expect(panelMetrics!.selectSize).toBe("14px");
  expect(panelMetrics!.inputSize).toBe("14px");
  expect(panelMetrics!.buttonSize).toBe("14px");
});

test("provider diagnostics cards expose scannable labeled facts", async ({ page }) => {
  await routeDiagnosticsWorkbench(page);
  await page.goto("/workbench");
  await page.getByRole("button", { name: /执行器体检/ }).click();
  await expect(page.getByRole("heading", { name: "执行器矩阵" })).toBeVisible();

  const firstCard = page.locator(".ct-provider-card").first();
  await expect(firstCard).toBeVisible();

  const requiredFacts = ["归属", "命令", "MCP", "产物", "JSON"];
  for (const fact of requiredFacts) {
    await expect(
      firstCard.locator(".ct-provider-kv-label", { hasText: fact }),
      `${fact} should be a dedicated label, not inline grey prose`,
    ).toBeVisible();
  }

  await expect(firstCard.locator(".ct-provider-kv-value").first()).toBeVisible();
  await expect(firstCard.locator(".ct-provider-section-title", { hasText: "启动探测" })).toBeVisible();
});

test("workbench removes noninteractive summary cards", async ({ page }) => {
  await routeDiagnosticsWorkbench(page);
  await page.goto("/workbench");

  await expect(page.locator(".ct-status-grid")).toHaveCount(0);
  await expect(page.getByText("工作台系统门禁")).toHaveCount(0);
  await expect(page.getByText("工作流能力")).toHaveCount(0);
  await expect(page.getByText("核心工作流就绪度")).toHaveCount(0);
});

test("workflow selector shows Chinese workflow names", async ({ page }) => {
  await routeDiagnosticsWorkbench(page);
  await page.goto("/workbench");
  await expect(page.getByRole("heading", { name: "任务运行" })).toBeVisible();

  const optionTexts = await page
    .getByLabel("工作流")
    .locator("option")
    .evaluateAll((options) => options.map((option) => option.textContent?.trim() ?? ""));

  expect(optionTexts).toContain("MR 黑盒测试工作流");
  expect(optionTexts).not.toContain("MR Black-box Test Workflow");
  expect(optionTexts).not.toContain("mr-blackbox-workflow");
});

test("workbench panel overlay does not wash out dark text", async ({ page }) => {
  await page.goto("/workbench");
  await expect(page.getByRole("heading", { name: "任务运行" })).toBeVisible();

  const overlayOpacity = await page
    .locator(".ct-workbench-panel")
    .first()
    .evaluate((panel) => Number(getComputedStyle(panel, "::after").opacity));

  expect(overlayOpacity).toBeLessThanOrEqual(0.18);
});

test("workbench hero title and intro are crisp above decorative layers", async ({ page }) => {
  await page.goto("/workbench");
  await expect(page.getByRole("heading", { name: "智能体编排台" })).toBeVisible();

  const metrics = await page.evaluate(() => {
    const hero = document.querySelector(".ct-workbench-hero");
    const title = hero?.querySelector("h1");
    const intro = hero?.querySelector("p.text-on-surface-variant");
    const titleColor = title ? getComputedStyle(title).color : "";
    const introColor = intro ? getComputedStyle(intro).color : "";
    const titleZ = title ? getComputedStyle(title).zIndex : "";
    const afterZ = hero ? getComputedStyle(hero, "::after").zIndex : "";
    return { titleColor, introColor, titleZ, afterZ };
  });

  const titleRgb = parseRgb(metrics.titleColor);
  const introRgb = parseRgb(metrics.introColor);

  expect(titleRgb, `title color should parse: ${metrics.titleColor}`).not.toBeNull();
  expect(titleRgb!.a, "title must not be translucent").toBeGreaterThanOrEqual(0.95);
  expect(titleRgb!.r, "title should be dark enough").toBeLessThanOrEqual(45);
  expect(introRgb, `intro color should parse: ${metrics.introColor}`).not.toBeNull();
  expect(introRgb!.a, "intro must not be translucent").toBeGreaterThanOrEqual(0.9);
  expect(introRgb!.r, "intro should be darker than washed helper text").toBeLessThanOrEqual(75);
  expect(Number(metrics.titleZ)).toBeGreaterThan(Number(metrics.afterZ));
});

test("workbench hero stays visible across reloads after motion setup", async ({ page }) => {
  for (let index = 0; index < 3; index += 1) {
    await page.goto("/workbench");
    await page.waitForTimeout(700);
    await expect(page.getByRole("heading", { name: "智能体编排台" })).toBeVisible();

    const metrics = await page.evaluate(() => {
      const hero = document.querySelector(".ct-workbench-hero");
      const title = hero?.querySelector("h1");
      const heroStyle = hero ? getComputedStyle(hero) : null;
      const titleBox = title?.getBoundingClientRect();
      return {
        heroOpacity: heroStyle ? Number(heroStyle.opacity) : 0,
        heroVisibility: heroStyle?.visibility ?? "",
        titleWidth: titleBox?.width ?? 0,
        titleHeight: titleBox?.height ?? 0,
      };
    });

    expect(metrics.heroVisibility).toBe("visible");
    expect(metrics.heroOpacity).toBeGreaterThanOrEqual(0.99);
    expect(metrics.titleWidth).toBeGreaterThan(80);
    expect(metrics.titleHeight).toBeGreaterThan(20);
  }
});
