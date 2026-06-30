import { test, expect } from "@playwright/test";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

test.describe("Health smoke tests", () => {
  test("homepage loads and shows navigation", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(page).toHaveTitle(/CodeTalk/i);
    await expect(page.locator("text=工作空间").first()).toBeVisible();
  });

  test("backend health endpoint responds", async ({ request }) => {
    const resp = await request.get(`${backendBase}/health`);
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    expect(body.status).toBe("ok");
  });

  test("settings page loads", async ({ page }) => {
    await page.goto("/settings", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: "设置", exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "先配置你平时用的 Agent" })).toBeVisible();
    await page.getByRole("button", { name: /高级：Workbench 探测配置/ }).click();
    await expect(page.getByLabel("Claude Code command")).toBeVisible();
  });

  test("workspaces list page loads", async ({ page }) => {
    await page.goto("/workspaces", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).toBeVisible();
  });
});
