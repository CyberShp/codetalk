import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

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
