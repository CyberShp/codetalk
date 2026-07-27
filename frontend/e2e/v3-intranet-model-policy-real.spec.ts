import { expect, test } from "@playwright/test";

import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "V3 intranet deployment network policy browser contract",
});

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;
const deepSeekApiKey = process.env.CODETALK_E2E_DEEPSEEK_API_KEY ?? "";
const runRealCodexProbe = process.env.CODETALK_E2E_CODEX_REAL === "1";

test("connects to an approved DeepSeek endpoint through the visible settings form", async ({ page }) => {
  test.skip(!deepSeekApiKey, "requires CODETALK_E2E_DEEPSEEK_API_KEY");
  test.setTimeout(90_000);

  await page.goto("/settings", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /可选：内置模型与 RAG 检索/ }).click();
  await page.getByRole("button", { name: "新增" }).click();

  const form = page.locator("form").filter({ hasText: "新增 LLM 配置" });
  await form.getByPlaceholder("如：Claude / GPT-4o").fill(`DeepSeek Flash 实网探测 ${Date.now()}`);
  await form.getByPlaceholder("https://api.openai.com/v1").fill("https://api.deepseek.com/v1");
  await form.getByPlaceholder(/sk-|Ollama/).fill(deepSeekApiKey);
  await form.getByRole("textbox", { name: "gpt-4o", exact: true }).fill("deepseek-v4-flash");
  await form.getByRole("button", { name: "测试连接" }).hover();
  await form.getByRole("button", { name: "测试连接" }).click();

  await expect(form).toContainText("连接成功", { timeout: 75_000 });
  await expect(page.locator("body")).not.toContainText(deepSeekApiKey);
});

test("runs a real Codex CLI model probe through the approved deployment boundary", async ({ page, request }) => {
  test.skip(!runRealCodexProbe, "requires CODETALK_E2E_CODEX_REAL=1");
  test.setTimeout(120_000);
  const runtimeName = `Codex 实网策略探测 ${Date.now()}`;
  let runtimeId = "";

  try {
    await page.goto("/settings", { waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: "Codex CLI" }).click();
    await page.getByLabel("显示名称").fill(runtimeName);
    await page.getByRole("button", { name: "保存" }).click();

    const runtimeCard = page.locator('[data-testid^="agent-runtime-card-"]').filter({ hasText: runtimeName });
    await expect(runtimeCard).toContainText("联网 Agent", { timeout: 30_000 });
    runtimeId = (await runtimeCard.getAttribute("data-testid"))?.replace("agent-runtime-card-", "") ?? "";
    await runtimeCard.getByRole("button", { name: "测试" }).hover();
    await runtimeCard.getByRole("button", { name: "测试" }).click();
    await expect(runtimeCard).toContainText("Codex 已登录，真实模型请求可用", { timeout: 90_000 });
  } finally {
    if (runtimeId) {
      await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtimeId)}`);
    }
  }
});

test("rejects an unapproved model endpoint from the visible settings form before connecting", async ({ page }) => {
  await page.goto("/settings", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /可选：内置模型与 RAG 检索/ }).click();
  await page.getByRole("button", { name: "新增" }).click();

  const form = page.locator("form").filter({ hasText: "新增 LLM 配置" });
  await form.getByPlaceholder("如：Claude / GPT-4o").fill(`未批准端点 ${Date.now()}`);
  await form.getByPlaceholder("https://api.openai.com/v1").fill("https://example.com/v1");
  await form.getByPlaceholder(/sk-|Ollama/).fill("test-key-not-sent");
  await form.getByRole("textbox", { name: "gpt-4o", exact: true }).fill("deepseek-v4-flash");
  await form.getByRole("button", { name: "测试连接" }).hover();
  await form.getByRole("button", { name: "测试连接" }).click();

  await expect(form).toContainText("部署网络策略阻止模型连接：模型地址未获管理员批准。请联系管理员检查模型地址和部署出站边界。");
  await expect(form).not.toContainText("host_not_allowlisted");
  await expect(page.locator("body")).not.toContainText("test-key-not-sent");
});

test("deployment intranet policy is read-only, survives refresh, and separates CLI readiness from the built-in model", async ({ page, request }) => {
  test.setTimeout(45_000);

  const policyResponse = await request.get(`${backendBase}/api/settings/network-policy`);
  expect(policyResponse.ok(), "设置页必须使用部署只读网络策略接口").toBeTruthy();
  const policy = await policyResponse.json() as {
    mode: string;
    source: string;
    cli_network_ready: boolean;
    boundary: string;
  };
  expect(policy.source).toBe("deployment");
  expect(policy.mode).toBe("intranet");

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/settings", { waitUntil: "domcontentloaded" });
  const policyToggle = page.getByRole("button", { name: /部署网络策略/ });
  await expect(policyToggle).toContainText("管理员部署配置");
  await policyToggle.hover();
  await policyToggle.click();

  const panel = page.getByTestId("deployment-network-policy");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("内网模式");
  await expect(panel).toContainText("模型访问");
  await expect(panel).toContainText("CLI Agent");
  await expect(panel).toContainText("企业代理");
  await expect(panel).toContainText("CA 证书");
  await expect(panel).toContainText("遥测");
  await expect(panel).toContainText("远程追踪");
  await expect(panel).toContainText("Hosted MCP");
  await expect(panel).not.toContainText("http://");
  await expect(panel).not.toContainText("https://");

  if (!policy.cli_network_ready && policy.boundary === "none") {
    await expect(panel).toContainText("CLI Agent 已被部署策略阻断");
    await expect(panel).toContainText("CLI Agent 的网络状态不会把内置模型误报为不可用。");
  }

  await page.reload({ waitUntil: "domcontentloaded" });
  const reloadedToggle = page.getByRole("button", { name: /部署网络策略/ });
  await expect(reloadedToggle).toContainText("管理员部署配置");
  await reloadedToggle.click();
  const reloadedPanel = page.getByTestId("deployment-network-policy");
  await expect(reloadedPanel).toContainText("内网模式");
});

test("runtime networking defaults to approved-boundary mode and persists an explicit offline Agent", async ({ page, request }) => {
  const runtimeName = `离线 Agent 浏览器契约 ${Date.now()}`;
  let runtimeId = "";
  try {
    await page.goto("/settings", { waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: "自定义命令" }).click();
    await page.getByLabel("显示名称").fill(runtimeName);
    await page.getByLabel("命令").fill("printf");
    await page.getByRole("button", { name: "高级选项" }).click();

    const networkSelect = page.getByLabel("网络访问方式");
    await expect(networkSelect).toHaveValue("networked");
    await expect(page.getByText("联网 Agent 仅能使用管理员部署批准的网络边界。")).toBeVisible();
    await networkSelect.selectOption("offline");
    await expect(page.getByText("离线 Agent 将被 OS 网络隔离")).toBeVisible();
    await page.getByRole("button", { name: "保存" }).click();
    await expect(page.getByText(runtimeName)).toBeVisible({ timeout: 30_000 });

    const runtimesResponse = await request.get(`${backendBase}/api/settings/agent-runtimes`);
    expect(runtimesResponse.ok()).toBeTruthy();
    const runtimes = await runtimesResponse.json() as {
      items: Array<{ id: string; name: string; requires_network?: boolean }>;
    };
    const created = runtimes.items.find((runtime) => runtime.name === runtimeName);
    expect(created, "浏览器创建的 Agent 必须由后端持久化").toBeTruthy();
    expect(created?.requires_network, "离线选择必须真实送达并被后端保存").toBe(false);
    runtimeId = created?.id || "";

    await page.reload({ waitUntil: "domcontentloaded" });
    const runtimeCard = page.getByTestId(`agent-runtime-card-${runtimeId}`);
    await expect(runtimeCard).toContainText("离线 Agent");
  } finally {
    if (runtimeId) {
      await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtimeId)}`);
    }
  }
});
