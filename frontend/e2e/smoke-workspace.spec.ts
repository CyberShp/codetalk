import { test, expect } from "@playwright/test";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

test.describe("Workspace smoke tests", () => {
  test("create workspace page loads with form", async ({ page }) => {
    await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
    await expect(page.locator("input, textarea").first()).toBeVisible();
  });

  test("workspace API returns list", async ({ request }) => {
    const resp = await request.get(`${backendBase}/api/workspaces`);
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    expect(Array.isArray(body)).toBeTruthy();
  });

  test("tasks page loads", async ({ page }) => {
    await page.goto("/tasks", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).toBeVisible();
  });

  test("settings API returns LLM configs", async ({ request }) => {
    const resp = await request.get(`${backendBase}/api/settings/llm`);
    expect(resp.ok()).toBeTruthy();
  });
});
