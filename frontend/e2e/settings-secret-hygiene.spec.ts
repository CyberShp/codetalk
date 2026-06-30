import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

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

  await expect(savedRuntime).toContainText("探测中...", { timeout: 15_000 });
  await expect(savedRuntime).toContainText("不可用：probe failed", { timeout: 15_000 });
  await expect(savedRuntime).toContainText("--api-key <redacted>");
  await expect(savedRuntime).toContainText("token=<redacted>");
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
