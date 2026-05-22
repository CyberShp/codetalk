import { test, expect } from "@playwright/test";

test.describe("Health smoke tests", () => {
  test("homepage loads and shows navigation", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveTitle(/CodeTalk/i);
    await expect(page.locator("text=工作空间").first()).toBeVisible();
  });

  test("backend health endpoint responds", async ({ request }) => {
    const resp = await request.get("http://localhost:8100/health");
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    expect(body.status).toBe("ok");
  });

  test("settings page loads", async ({ page }) => {
    await page.goto("/settings");
    await expect(page.locator("h1, h2").first()).toBeVisible();
  });

  test("workspaces list page loads", async ({ page }) => {
    await page.goto("/workspaces");
    await expect(page.locator("body")).toBeVisible();
  });
});
