import { expect, test } from "@playwright/test";

test("home hero speaks to the broader AI testing workbench", async ({ page }) => {
  await page.route("**/api/workspaces", async (route) => {
    await route.fulfill({ json: [] });
  });

  await page.goto("/");

  await expect(page.getByText("AI 测试协同工作台")).toBeVisible();
  await expect(page.getByRole("heading", { name: /把代码理解\s*变成测试行动/ })).toBeVisible();
  await expect(page.getByText("需求、代码、工具执行器和测试证据")).toBeVisible();
  await expect(page.getByText("AI 测试中枢")).toBeVisible();
  await expect(page.getByText("CODETALK AI OS")).toBeVisible();
  await expect(page.getByText("Agent 编排")).toBeVisible();
  await expect(page.getByLabel("AI 测试中枢视觉面板").getByText("证据报告")).toBeVisible();
});
