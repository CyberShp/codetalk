import { expect, test } from "@playwright/test";

import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "V3 intranet model-route policy browser E2E",
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

  await expect(form).toContainText("运行时出站策略拒绝：host_not_allowlisted");
  await expect(page.locator("body")).not.toContainText("test-key-not-sent");
});
