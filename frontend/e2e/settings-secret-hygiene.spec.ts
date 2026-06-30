import { expect, test } from "@playwright/test";

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

  await savedRow.getByTitle("编辑").hover();
  await savedRow.getByTitle("编辑").click();
  const editForm = page.locator("form").filter({ hasText: "编辑 LLM 配置" });
  const reopenedApiKeyInput = editForm.getByPlaceholder(/留空则保持原密钥不变/);
  await expect(reopenedApiKeyInput).toHaveAttribute("type", "password");
  await expect(reopenedApiKeyInput).toHaveValue("");
  await expect(page.locator("body")).not.toContainText(secret);
});
