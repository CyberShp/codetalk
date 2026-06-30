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

  test("workspace materials can be added, deactivated, restored after reload, and deleted through the UI", async ({
    page,
    request,
  }) => {
    const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-materials-")));
    fs.writeFileSync(path.join(repo, "README.md"), "materials lifecycle e2e\n", "utf8");
    const materialPath = path.join(repo, "requirements.md");
    fs.writeFileSync(
      materialPath,
      "# Requirements\n\nNever expose workspace materials outside the selected project.\n",
      "utf8",
    );
    const workspaceName = `materials-workspace-${Date.now()}`;

    await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
    await page.getByPlaceholder(/项目 A/).fill(workspaceName);
    await page.getByPlaceholder(/本地文件夹路径/).fill(repo);
    await page.getByRole("button", { name: "创建工作空间" }).hover();
    await page.getByRole("button", { name: "创建工作空间" }).click();
    await page.waitForURL(/\/workspaces\/[0-9a-f-]{36}$/, { timeout: 30_000 });
    const workspaceUrl = page.url();
    const workspaceId = workspaceUrl.split("/").pop() ?? "";
    await expect(page.getByText(workspaceName)).toBeVisible({ timeout: 30_000 });

    await page.getByRole("button", { name: /材料 \(0\)/ }).hover();
    await page.getByRole("button", { name: /材料 \(0\)/ }).click();
    await page.getByPlaceholder(/输入文件绝对路径/).fill(materialPath);
    await page.getByRole("button", { name: "添加" }).hover();
    await page.getByRole("button", { name: "添加" }).click();

    await expect(page.getByRole("button", { name: /材料 \(1\)/ })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText("requirements.md")).toBeVisible();
    await expect(page.getByText("requirements").first()).toBeVisible();
    await expect(page.getByText("1 个活跃材料将参与分析")).toBeVisible();

    await page.getByTitle("已激活（参与对话上下文）").uncheck();
    await expect(page.getByTitle("已停用（不参与对话）")).toBeVisible({
      timeout: 15_000,
    });

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: /材料 \(1\)/ }).click();
    await expect(page.getByText("requirements.md")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTitle("已停用（不参与对话）")).toBeVisible();
    await expect(page.getByText("1 个活跃材料将参与分析")).toHaveCount(0);

    const materialRow = page.locator("div").filter({ hasText: "requirements.md" }).filter({ hasText: "requirements" }).first();
    await materialRow.hover();
    page.once("dialog", async (dialog) => {
      expect(dialog.message()).toContain("requirements.md");
      await dialog.dismiss();
    });
    await page.getByTitle("删除材料").click();
    await expect(page.getByText("requirements.md")).toBeVisible();
    expect(fs.existsSync(materialPath)).toBe(true);

    await materialRow.hover();
    page.once("dialog", async (dialog) => {
      expect(dialog.message()).toContain("requirements.md");
      await dialog.accept();
    });
    await page.getByTitle("删除材料").click();
    await expect(page.getByText("尚未上传任何材料")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("button", { name: /材料 \(0\)/ })).toBeVisible();
    expect(fs.existsSync(materialPath)).toBe(false);

    const workspaceResp = await request.get(`${backendBase}/api/workspaces/${workspaceId}`);
    expect(workspaceResp.ok()).toBeTruthy();
    const workspace = (await workspaceResp.json()) as { materials: Array<{ filename: string }> };
    expect(workspace.materials).toEqual([]);
  });

  test("workspace can be reindexed through the UI and remains searchable", async ({ page }) => {
    const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-reindex-")));
    fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
    fs.writeFileSync(path.join(repo, "README.md"), "reindex workspace e2e\n", "utf8");
    fs.writeFileSync(
      path.join(repo, "lib", "nvmf", "reindex.c"),
      "int codetalk_reindex_probe(void) { return 7; }\n",
      "utf8",
    );
    const workspaceName = `reindex-workspace-${Date.now()}`;

    await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
    await page.getByPlaceholder(/项目 A/).fill(workspaceName);
    await page.getByPlaceholder(/本地文件夹路径/).fill(repo);
    await page.getByRole("button", { name: "创建工作空间" }).hover();
    await page.getByRole("button", { name: "创建工作空间" }).click();
    await page.waitForURL(/\/workspaces\/[0-9a-f-]{36}$/, { timeout: 30_000 });
    await expect(page.getByText(workspaceName)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("已索引")).toBeVisible({ timeout: 120_000 });

    const reindexButton = page.getByRole("button", { name: "重新索引" });
    await reindexButton.hover();
    await reindexButton.click();
    await expect(reindexButton).toBeDisabled();
    await expect(page.getByText(/索引中/)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("已索引")).toBeVisible({ timeout: 120_000 });
    await expect(reindexButton).toBeEnabled();

    await page.getByRole("button", { name: "源码搜索" }).hover();
    await page.getByRole("button", { name: "源码搜索" }).click();
    await page.getByLabel("源码搜索").fill("codetalk_reindex_probe");
    await page.getByRole("button", { name: "搜索源码" }).hover();
    await page.getByRole("button", { name: "搜索源码" }).click();
    const result = page.locator("button").filter({ hasText: "reindex.c" }).first();
    await expect(result).toBeVisible({ timeout: 20_000 });
    await result.hover();
    await result.click();
    await expect(page.getByText("int codetalk_reindex_probe(void) { return 7; }")).toBeVisible({
      timeout: 10_000,
    });
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
