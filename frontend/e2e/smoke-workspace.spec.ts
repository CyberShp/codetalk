import { test, expect } from "@playwright/test";

test.describe("Workspace smoke tests", () => {
  test("create workspace page loads with form", async ({ page }) => {
    await page.goto("/workspaces/new");
    await expect(page.locator("input, textarea").first()).toBeVisible();
  });

  test("workspace API returns list", async ({ request }) => {
    const resp = await request.get("http://localhost:8100/api/workspaces");
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    expect(Array.isArray(body)).toBeTruthy();
  });

  test("tasks page loads", async ({ page }) => {
    await page.goto("/tasks");
    await expect(page.locator("body")).toBeVisible();
  });

  test("settings API returns LLM configs", async ({ request }) => {
    const resp = await request.get(
      "http://localhost:8100/api/settings/llm-configs",
    );
    expect(resp.ok()).toBeTruthy();
  });
});
