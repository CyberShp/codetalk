import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

async function expectBrowserStorageNotToContain(page: Page, secret: string) {
  const storageText = await page.evaluate(() =>
    JSON.stringify({
      localStorage: Object.fromEntries(Array.from({ length: window.localStorage.length }, (_, index) => {
        const key = window.localStorage.key(index) ?? "";
        return [key, window.localStorage.getItem(key)];
      })),
      sessionStorage: Object.fromEntries(Array.from({ length: window.sessionStorage.length }, (_, index) => {
        const key = window.sessionStorage.key(index) ?? "";
        return [key, window.sessionStorage.getItem(key)];
      })),
    }),
  );
  expect(storageText).not.toContain(secret);
}

test("settings LLM key stays masked and is not rendered after save/edit", async ({ page }) => {
  const secret = `sk-settings-ui-${Date.now()}`;
  const configName = `ui-secret-hygiene-${Date.now()}`;

  await page.goto("/settings", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /可选：内置模型与 RAG 检索/ }).click();
  await page.getByRole("button", { name: "新增" }).click();

  const form = page.locator("form").filter({ hasText: "新增 LLM 配置" });
  await form.getByPlaceholder("如：Claude / GPT-4o").fill(configName);
  await form.getByPlaceholder("https://api.openai.com/v1").fill("https://llm.example/v1");
  const apiKeyInput = form.getByPlaceholder(/sk-|Ollama/);
  await expect(apiKeyInput).toHaveAttribute("type", "password");
  await apiKeyInput.fill(secret);
  await expect(apiKeyInput).toHaveValue(secret);

  await form.getByRole("button", { name: "显示 API 密钥" }).hover();
  await form.getByRole("button", { name: "显示 API 密钥" }).click();
  await expect(apiKeyInput).toHaveAttribute("type", "text");
  await form.getByRole("button", { name: "隐藏 API 密钥" }).click();
  await expect(apiKeyInput).toHaveAttribute("type", "password");

  await form.getByRole("textbox", { name: "gpt-4o", exact: true }).fill("deepseek-chat");
  await form.getByRole("button", { name: "保存配置" }).click();

  const savedRow = page.locator("div", { hasText: configName }).filter({ hasText: "deepseek-chat" }).first();
  await expect(savedRow).toBeVisible();
  await expect(page.locator("body")).not.toContainText(secret);
  await expectBrowserStorageNotToContain(page, secret);

  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /可选：内置模型与 RAG 检索/ }).click();
  const reloadedRow = page.locator("div", { hasText: configName }).filter({ hasText: "deepseek-chat" }).first();
  await expect(reloadedRow).toBeVisible();
  await expect(page.locator("body")).not.toContainText(secret);
  await expectBrowserStorageNotToContain(page, secret);

  await reloadedRow.getByTitle("编辑").hover();
  await reloadedRow.getByTitle("编辑").click();
  const editForm = page.locator("form").filter({ hasText: "编辑 LLM 配置" });
  const reopenedApiKeyInput = editForm.getByPlaceholder(/留空则保持原密钥不变/);
  await expect(reopenedApiKeyInput).toHaveAttribute("type", "password");
  await expect(reopenedApiKeyInput).toHaveValue("");
  await expect(page.locator("body")).not.toContainText(secret);
  await expectBrowserStorageNotToContain(page, secret);
});

test("settings prevents duplicate LLM saves from a real double click", async ({ page }) => {
  const secret = `settings-double-save-key-${Date.now()}`;
  const configName = `ui-llm-double-save-${Date.now()}`;
  const modelName = `deepseek-double-save-${Date.now()}`;

  await page.goto("/settings", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /可选：内置模型与 RAG 检索/ }).click();
  await page.getByRole("button", { name: "新增" }).click();

  const form = page.locator("form").filter({ hasText: "新增 LLM 配置" });
  await form.getByPlaceholder("如：Claude / GPT-4o").fill(configName);
  await form.getByPlaceholder("https://api.openai.com/v1").fill("https://llm.example/v1");
  await form.getByPlaceholder(/sk-|Ollama/).fill(secret);
  await form.getByRole("textbox", { name: "gpt-4o", exact: true }).fill(modelName);

  const createRequests: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/settings/llm"
    ) {
      createRequests.push(request.url());
    }
  });
  const createRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/settings/llm",
  );

  await form.getByRole("button", { name: "保存配置" }).hover();
  await form.getByRole("button", { name: "保存配置" }).dblclick();
  await createRequest;
  await expect(form.getByRole("button", { name: "保存配置" })).toBeDisabled();

  const savedRow = page.locator("div", { hasText: configName }).filter({ hasText: modelName }).first();
  await expect(savedRow).toBeVisible({ timeout: 15_000 });
  await expect.poll(() => createRequests.length).toBe(1);
  await expect(page.locator("body")).not.toContainText(secret);
  await expectBrowserStorageNotToContain(page, secret);
});

test("settings active chat model selection persists after reload", async ({ page }) => {
  const configName = `ui-active-model-${Date.now()}`;
  const modelName = `deepseek-active-${Date.now()}`;

  await page.goto("/settings", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /可选：内置模型与 RAG 检索/ }).click();
  await page.getByRole("button", { name: "新增" }).click();

  const form = page.locator("form").filter({ hasText: "新增 LLM 配置" });
  await form.getByPlaceholder("如：Claude / GPT-4o").fill(configName);
  await form.getByPlaceholder("https://api.openai.com/v1").fill("https://llm.example/v1");
  await form.getByPlaceholder(/sk-|Ollama/).fill(`sk-active-model-${Date.now()}`);
  await form.getByRole("textbox", { name: "gpt-4o", exact: true }).fill(modelName);
  await form.getByRole("button", { name: "保存配置" }).click();

  const activeModelSelect = page.locator("select").filter({
    has: page.locator("option", { hasText: configName }),
  });
  await expect(activeModelSelect).toBeEnabled({ timeout: 15_000 });
  await activeModelSelect.selectOption({ label: `${configName} (${modelName})` });
  await expect(activeModelSelect).toHaveValue(/.+/);
  const selectedModelId = await activeModelSelect.inputValue();

  const savedRow = page.locator("div", { hasText: configName }).filter({ hasText: modelName }).first();
  await expect(savedRow).toContainText("活跃", { timeout: 15_000 });

  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /可选：内置模型与 RAG 检索/ }).click();
  const reloadedSelect = page.locator("select").filter({
    has: page.locator("option", { hasText: configName }),
  });
  await expect(reloadedSelect).toHaveValue(selectedModelId);
  const reloadedRow = page.locator("div", { hasText: configName }).filter({ hasText: modelName }).first();
  await expect(reloadedRow).toContainText("活跃", { timeout: 15_000 });
});

test("settings LLM deletion requires confirmation and persists after reload", async ({
  page,
  request,
}) => {
  const configName = `ui-delete-model-${Date.now()}`;
  const modelName = `delete-model-${Date.now()}`;

  await page.goto("/settings", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /可选：内置模型与 RAG 检索/ }).click();
  await page.getByRole("button", { name: "新增" }).click();

  const form = page.locator("form").filter({ hasText: "新增 LLM 配置" });
  await form.getByPlaceholder("如：Claude / GPT-4o").fill(configName);
  await form.getByPlaceholder("https://api.openai.com/v1").fill("https://llm.example/v1");
  await form.getByPlaceholder(/sk-|Ollama/).fill(`delete-key-${Date.now()}`);
  await form.getByRole("textbox", { name: "gpt-4o", exact: true }).fill(modelName);
  await form.getByRole("button", { name: "保存配置" }).hover();
  await form.getByRole("button", { name: "保存配置" }).click();

  const savedRow = page
    .locator("p.text-sm", { hasText: configName })
    .locator("xpath=ancestor::div[contains(@class,'bg-surface-container')][1]");
  const deleteButton = savedRow.locator('button[title="删除"]');
  await expect(savedRow).toBeVisible({ timeout: 15_000 });

  page.once("dialog", async (dialog) => {
    expect(dialog.type()).toBe("confirm");
    expect(dialog.message()).toContain("确定要删除此配置吗");
    await dialog.dismiss();
  });
  await deleteButton.hover();
  await deleteButton.click();
  await expect(savedRow).toBeVisible();

  page.once("dialog", async (dialog) => {
    expect(dialog.type()).toBe("confirm");
    expect(dialog.message()).toContain("确定要删除此配置吗");
    await dialog.accept();
  });
  await deleteButton.hover();
  await deleteButton.click();
  await expect(page.getByText(configName)).toHaveCount(0, { timeout: 15_000 });

  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /可选：内置模型与 RAG 检索/ }).click();
  await expect(page.getByText(configName)).toHaveCount(0);

  const listResp = await request.get(`${backendBase}/api/settings/llm`);
  expect(listResp.ok()).toBeTruthy();
  const configs = (await listResp.json()) as Array<{ name: string; model: string }>;
  expect(configs.some((item) => item.name === configName || item.model === modelName)).toBe(false);
});

test("settings agent runtime env values are not rendered after save", async ({ page }) => {
  const secret = `sk-agent-ui-${Date.now()}`;
  const runtimeName = `ui-agent-secret-${Date.now()}`;

  await page.goto("/settings", { waitUntil: "domcontentloaded" });
  await page.getByPlaceholder("例如 Claude Code").fill(runtimeName);
  await page.getByPlaceholder("ccr / opencode / nga").fill("python3");
  await page.getByPlaceholder("code 或 run").fill("--version");
  await page.getByRole("button", { name: "高级选项" }).click();
  await page.getByPlaceholder(/HTTPS_PROXY/).fill(
    JSON.stringify(
      {
        AGENT_TOKEN: secret,
        SAFE_FLAG: "enabled",
      },
      null,
      2,
    ),
  );

  await page.getByRole("button", { name: "保存" }).click();

  const savedRuntime = page.locator("div", { hasText: runtimeName }).filter({ hasText: "python3 --version" }).first();
  await expect(savedRuntime).toBeVisible();
  await savedRuntime.getByRole("button", { name: "测试" }).hover();
  await expect(page.locator("body")).not.toContainText(secret);
  await expectBrowserStorageNotToContain(page, secret);

  await page.reload({ waitUntil: "domcontentloaded" });
  const reloadedRuntime = page.locator("div", { hasText: runtimeName }).filter({ hasText: "python3 --version" }).first();
  await expect(reloadedRuntime).toBeVisible();
  await expect(page.locator("body")).not.toContainText(secret);
  await expectBrowserStorageNotToContain(page, secret);
});

test("settings blocks malformed agent runtime env JSON with repair guidance", async ({
  page,
}) => {
  const runtimeName = `ui-agent-bad-env-${Date.now()}`;
  const createRequests: string[] = [];

  page.on("request", (req) => {
    if (
      req.method() === "POST" &&
      new URL(req.url()).pathname === "/api/settings/agent-runtimes"
    ) {
      createRequests.push(req.url());
    }
  });

  await page.goto("/settings", { waitUntil: "domcontentloaded" });
  await page.getByPlaceholder("例如 Claude Code").fill(runtimeName);
  await page.getByPlaceholder("ccr / opencode / nga").fill("python3");
  await page.getByPlaceholder("code 或 run").fill("--version");
  await page.getByRole("button", { name: "高级选项" }).hover();
  await page.getByRole("button", { name: "高级选项" }).click();
  await page.getByPlaceholder(/HTTPS_PROXY/).fill("{BROKEN_JSON");
  await page.getByRole("button", { name: "保存" }).hover();
  await page.getByRole("button", { name: "保存" }).click();

  const alert = page.locator('div[role="alert"]').filter({ hasText: "环境变量 JSON 格式错误" });
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("请填写 JSON 对象");
  await expect(alert).toContainText("HTTPS_PROXY");
  await expect(page.getByText(runtimeName)).toHaveCount(0);
  await expect.poll(() => createRequests.length).toBe(0);
});

test("settings prevents duplicate agent runtime deletion from a real double click", async ({
  page,
  request,
}) => {
  const runtimeName = `ui-agent-delete-${Date.now()}`;

  await page.goto("/settings", { waitUntil: "domcontentloaded" });
  await page.getByPlaceholder("例如 Claude Code").fill(runtimeName);
  await page.getByPlaceholder("ccr / opencode / nga").fill("python3");
  await page.getByPlaceholder("code 或 run").fill("--version");
  await page.getByRole("button", { name: "保存" }).hover();
  await page.getByRole("button", { name: "保存" }).click();

  const savedRuntime = page
    .locator("div.rounded-xl.border")
    .filter({ has: page.locator("strong", { hasText: runtimeName }) })
    .filter({ hasText: "python3 --version" })
    .first();
  await expect(savedRuntime).toBeVisible({ timeout: 15_000 });

  const deleteRequests: string[] = [];
  page.on("request", (req) => {
    if (
      req.method() === "DELETE" &&
      new URL(req.url()).pathname.startsWith("/api/settings/agent-runtimes/")
    ) {
      deleteRequests.push(req.url());
    }
  });
  const firstDelete = page.waitForRequest(
    (req) =>
      req.method() === "DELETE" &&
      new URL(req.url()).pathname.startsWith("/api/settings/agent-runtimes/"),
  );

  let confirmDialogs = 0;
  page.on("dialog", async (dialog) => {
    confirmDialogs += 1;
    expect(dialog.type()).toBe("confirm");
    expect(dialog.message()).toContain("确定要删除这个 AI 线程执行器吗");
    await dialog.accept();
  });

  await savedRuntime.getByRole("button", { name: "删除" }).hover();
  await savedRuntime.getByRole("button", { name: "删除" }).dblclick();
  await firstDelete;

  await expect(page.getByText(runtimeName)).toHaveCount(0, { timeout: 15_000 });
  await expect.poll(() => deleteRequests.length).toBe(1);
  expect(confirmDialogs).toBe(1);

  const listResp = await request.get(`${backendBase}/api/settings/agent-runtimes`);
  expect(listResp.ok()).toBeTruthy();
  const runtimes = (await listResp.json()) as { items: Array<{ name: string }> };
  expect(runtimes.items.some((item) => item.name === runtimeName)).toBe(false);
});

test("settings agent runtime probe shows redacted actionable failure output", async ({ page }) => {
  const secret = `agent-probe-secret-${Date.now()}`;
  const runtimeName = `ui-agent-probe-failure-${Date.now()}`;
  const scriptDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-probe-")));
  const scriptPath = path.join(scriptDir, "probe_failure.py");
  fs.writeFileSync(
    scriptPath,
    [
      "import sys",
      `print('probe failed --api-key ${secret}; token=${secret}', file=sys.stderr)`,
      "raise SystemExit(5)",
      "",
    ].join("\n"),
    "utf8",
  );

  await page.goto("/settings", { waitUntil: "domcontentloaded" });
  await page.getByPlaceholder("例如 Claude Code").fill(runtimeName);
  await page.getByPlaceholder("ccr / opencode / nga").fill("python3");
  await page.getByPlaceholder("code 或 run").fill(scriptPath);
  await page.getByRole("button", { name: "高级选项" }).click();
  await page.getByPlaceholder(/HTTPS_PROXY/).fill(
    JSON.stringify(
      {
        AGENT_TOKEN: secret,
      },
      null,
      2,
    ),
  );
  await page.getByRole("button", { name: "保存" }).click();

  const savedRuntime = page
    .locator("div.rounded-xl.border")
    .filter({ has: page.locator("strong", { hasText: runtimeName }) })
    .filter({ hasText: scriptPath })
    .first();
  await expect(savedRuntime).toBeVisible({ timeout: 15_000 });
  await expect(page.locator("body")).not.toContainText(secret);
  await expectBrowserStorageNotToContain(page, secret);

  await savedRuntime.getByRole("button", { name: "测试" }).hover();
  await savedRuntime.getByRole("button", { name: "测试" }).click();

  await expect(savedRuntime).toContainText("不可用：probe failed", { timeout: 15_000 });
  await expect(savedRuntime).toContainText("--api-key <redacted>");
  await expect(savedRuntime).toContainText(["token", "<redacted>"].join("="));
  await expect(page.locator("body")).not.toContainText(secret);
  await expectBrowserStorageNotToContain(page, secret);

  await page.reload({ waitUntil: "domcontentloaded" });
  const reloadedRuntime = page
    .locator("div.rounded-xl.border")
    .filter({ has: page.locator("strong", { hasText: runtimeName }) })
    .filter({ hasText: scriptPath })
    .first();
  await expect(reloadedRuntime).toBeVisible({ timeout: 15_000 });
  await expect(page.locator("body")).not.toContainText(secret);
  await expectBrowserStorageNotToContain(page, secret);
});
