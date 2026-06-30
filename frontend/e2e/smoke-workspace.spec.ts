import { test, expect } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

test.describe("Workspace smoke tests", () => {
  test("create workspace page loads with form", async ({ page }) => {
    await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
    await expect(page.locator("input, textarea").first()).toBeVisible();
  });

  test("create workspace reports a misspelled mounted path with repair guidance", async ({ page }) => {
    const workspaceName = `bad-spdk-path-${Date.now()}`;

    await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
    await page.getByPlaceholder(/项目 A/).fill(workspaceName);
    await page.getByPlaceholder(/本地文件夹路径/).fill("/Volums/Media/dpdk/spdk");
    await page.getByRole("button", { name: "创建工作空间" }).hover();
    await page.getByRole("button", { name: "创建工作空间" }).click();

    const alert = page.locator('div[role="alert"]').filter({ hasText: "修复建议" });
    await expect(alert).toContainText("代码路径不存在");
    await expect(alert).toContainText("/Volums/Media/dpdk/spdk");
    await expect(alert).toContainText("修复建议");
    await expect(alert).toContainText("/Volumes/...");
    await expect(page).toHaveURL(/\/workspaces\/new$/);
  });

  test("duplicate workspace creation offers a link to the existing workspace", async ({
    page,
    request,
  }) => {
    const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-dup-workspace-")));
    fs.writeFileSync(path.join(repo, "README.md"), "duplicate workspace e2e\n", "utf8");
    const firstName = `dup-workspace-${Date.now()}`;
    const duplicateName = `${firstName}-again`;

    await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
    await page.getByPlaceholder(/项目 A/).fill(firstName);
    await page.getByPlaceholder(/本地文件夹路径/).fill(repo);
    await page.getByRole("button", { name: "创建工作空间" }).hover();
    await page.getByRole("button", { name: "创建工作空间" }).click();
    await page.waitForURL(/\/workspaces\/[0-9a-f-]{36}$/, { timeout: 30_000 });
    const existingUrl = page.url();

    await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
    await page.getByPlaceholder(/项目 A/).fill(duplicateName);
    await page.getByPlaceholder(/本地文件夹路径/).fill(`${repo}/.`);
    await page.getByRole("button", { name: "创建工作空间" }).hover();
    await page.getByRole("button", { name: "创建工作空间" }).click();

    const alert = page.locator('div[role="alert"]').filter({ hasText: "该代码路径已存在工作空间" });
    await expect(alert).toContainText("该代码路径已存在工作空间");
    await expect(alert).not.toContainText("/Volums/...");
    const existingLink = page.getByRole("link", { name: new RegExp(`打开已有工作空间.*${firstName}`) });
    await expect(existingLink).toBeVisible();
    await expect(existingLink).toHaveAttribute("href", new URL(existingUrl).pathname);

    const listResp = await request.get(`${backendBase}/api/workspaces`);
    expect(listResp.ok()).toBeTruthy();
    const workspaces = (await listResp.json()) as Array<{ name: string; repo_path: string }>;
    expect(workspaces.filter((item) => item.name === firstName)).toHaveLength(1);
    expect(workspaces.find((item) => item.name === firstName)?.repo_path).toBe(repo);
    expect(workspaces.some((item) => item.name === duplicateName)).toBe(false);
  });

  test("workspace API returns list", async ({ request }) => {
    const resp = await request.get(`${backendBase}/api/workspaces`);
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    expect(Array.isArray(body)).toBeTruthy();
  });

  test("settings API returns LLM configs", async ({ request }) => {
    const resp = await request.get(`${backendBase}/api/settings/llm`);
    expect(resp.ok()).toBeTruthy();
  });
});
