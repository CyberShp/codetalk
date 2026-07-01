import { expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

async function noFrameworkOverlay(page: import("@playwright/test").Page) {
  await expect(
    page
      .locator("nextjs-portal")
      .filter({ hasText: /Unhandled Runtime Error|Build Error|Application error/i }),
  ).toHaveCount(0);
  await expect(page.getByText(/Unhandled Runtime Error|Build Error|Application error/i)).toHaveCount(0);
}

test("source preview opens an empty file with a valid line range through the UI", async ({ page }) => {
  test.setTimeout(180_000);

  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-empty-source-"));
  fs.writeFileSync(path.join(repo, "empty.c"), "", "utf8");
  fs.writeFileSync(path.join(repo, "README.md"), "empty source preview e2e\n", "utf8");
  const workspaceName = `empty-source-e2e-${Date.now()}`;

  await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
  await noFrameworkOverlay(page);
  await page.getByRole("button", { name: "创建工作空间" }).hover();
  await page.getByPlaceholder(/项目 A/).fill(workspaceName);
  await page.getByPlaceholder(/本地文件夹路径/).fill(repo);
  await page.getByRole("button", { name: "创建工作空间" }).click();
  await page.waitForURL(/\/workspaces\/[0-9a-f-]{36}$/, { timeout: 30_000 });
  await expect(page.getByText(workspaceName)).toBeVisible({ timeout: 30_000 });

  await expect
    .poll(async () => page.locator("body").innerText(), { timeout: 120_000 })
    .toMatch(/已索引/);

  await page.getByRole("button", { name: "源码搜索" }).click();
  const sourceSearch = page.getByLabel("源码搜索");
  await sourceSearch.fill("empty.c");
  await page.getByRole("button", { name: "搜索源码" }).hover();
  await page.getByRole("button", { name: "搜索源码" }).click();

  const result = page.locator("button").filter({ hasText: "empty.c" }).first();
  await expect(result).toBeVisible({ timeout: 20_000 });
  await result.hover();
  await result.click();

  await expect(page.getByText("empty.c").first()).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("1-1 / 0 行")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("1-0 / 0 行")).toHaveCount(0);
});

test("source preview opens a searched function with real surrounding code", async ({ page }) => {
  test.setTimeout(180_000);

  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-source-window-"));
  const sourceDir = path.join(repo, "lib", "nvmf");
  fs.mkdirSync(sourceDir, { recursive: true });
  fs.writeFileSync(path.join(repo, "README.md"), "source preview window e2e\n", "utf8");
  const sourceLines = Array.from({ length: 40 }, (_, index) => {
    const line = index + 1;
    if (line === 1) return "/* window-start-sentinel */";
    if (line === 21) return "int nvmf_source_window_target(void) { return 21; }";
    if (line === 40) return "/* window-end-sentinel */";
    return `int source_window_padding_${String(line).padStart(2, "0")}(void) { return ${line}; }`;
  });
  fs.writeFileSync(path.join(sourceDir, "preview.c"), `${sourceLines.join("\n")}\n`, "utf8");
  const workspaceName = `source-window-e2e-${Date.now()}`;

  await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
  await noFrameworkOverlay(page);
  await page.getByPlaceholder(/项目 A/).fill(workspaceName);
  await page.getByPlaceholder(/本地文件夹路径/).fill(repo);
  await page.getByRole("button", { name: "创建工作空间" }).hover();
  await page.getByRole("button", { name: "创建工作空间" }).click();
  await page.waitForURL(/\/workspaces\/[0-9a-f-]{36}$/, { timeout: 30_000 });
  await expect(page.getByText(workspaceName)).toBeVisible({ timeout: 30_000 });

  await expect
    .poll(async () => page.locator("body").innerText(), { timeout: 120_000 })
    .toMatch(/已索引/);

  await page.getByRole("button", { name: "源码搜索" }).hover();
  await page.getByRole("button", { name: "源码搜索" }).click();
  const sourceSearch = page.getByLabel("源码搜索");
  await sourceSearch.fill("nvmf_source_window_target");
  await sourceSearch.press("Enter");

  const result = page.locator("button").filter({ hasText: "lib/nvmf/preview.c" }).first();
  await expect(result).toBeVisible({ timeout: 20_000 });
  await expect(result).toContainText("L21");
  await expect(result).toContainText("nvmf_source_window_target");
  await result.hover();
  await result.click();

  await expect(page.getByText("lib/nvmf/preview.c").first()).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("1-40 / 40 行")).toBeVisible({ timeout: 10_000 });
  await expect(page.locator("pre")).toContainText("window-start-sentinel");
  await expect(page.locator("pre")).toContainText("int nvmf_source_window_target(void) { return 21; }");
  await expect(page.locator("pre")).toContainText("window-end-sentinel");
});

test("source preview degrades clearly when a linked source file is missing", async ({ page }) => {
  test.setTimeout(180_000);

  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-missing-source-"));
  fs.writeFileSync(path.join(repo, "README.md"), "missing linked source preview e2e\n", "utf8");
  const workspaceName = `missing-source-e2e-${Date.now()}`;

  await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
  await noFrameworkOverlay(page);
  await page.getByPlaceholder(/项目 A/).fill(workspaceName);
  await page.getByPlaceholder(/本地文件夹路径/).fill(repo);
  await page.getByRole("button", { name: "创建工作空间" }).hover();
  await page.getByRole("button", { name: "创建工作空间" }).click();
  await page.waitForURL(/\/workspaces\/[0-9a-f-]{36}$/, { timeout: 30_000 });
  await expect(page.getByText(workspaceName)).toBeVisible({ timeout: 30_000 });

  await expect
    .poll(async () => page.locator("body").innerText(), { timeout: 120_000 })
    .toMatch(/已索引/);
  const workspaceUrl = page.url();
  const missingPath = "lib/nvmf/deleted.c";

  await page.goto(`${workspaceUrl}?tab=source&sourcePath=${encodeURIComponent(missingPath)}&line=12`, {
    waitUntil: "domcontentloaded",
  });
  await noFrameworkOverlay(page);

  await expect(page.getByLabel("源码搜索")).toHaveValue(missingPath);
  await expect(page.getByText(`源码文件不存在：${missingPath}`)).toBeVisible({ timeout: 20_000 });
  await expect(page.locator("pre")).toHaveCount(0);
});

test("source search does not expose files outside the workspace through symlinks", async ({ page }) => {
  test.setTimeout(180_000);

  const root = fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-source-symlink-"));
  const repo = path.join(root, "repo");
  const outside = path.join(root, "outside-secret.txt");
  const marker = `leak-marker-token-${Date.now()}`;
  fs.mkdirSync(repo, { recursive: true });
  fs.writeFileSync(path.join(repo, "README.md"), "symlink source search e2e\n", "utf8");
  fs.writeFileSync(outside, `${marker}\n`, "utf8");
  fs.symlinkSync(outside, path.join(repo, "linked-secret.txt"));
  const workspaceName = `source-symlink-e2e-${Date.now()}`;

  await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
  await noFrameworkOverlay(page);
  await page.getByPlaceholder(/项目 A/).fill(workspaceName);
  await page.getByPlaceholder(/本地文件夹路径/).fill(repo);
  await page.getByRole("button", { name: "创建工作空间" }).hover();
  await page.getByRole("button", { name: "创建工作空间" }).click();
  await page.waitForURL(/\/workspaces\/[0-9a-f-]{36}$/, { timeout: 30_000 });
  await expect(page.getByText(workspaceName)).toBeVisible({ timeout: 30_000 });

  await expect
    .poll(async () => page.locator("body").innerText(), { timeout: 120_000 })
    .toMatch(/已索引/);

  await page.getByRole("button", { name: "源码搜索" }).hover();
  await page.getByRole("button", { name: "源码搜索" }).click();
  const sourceSearch = page.getByLabel("源码搜索");
  await sourceSearch.fill(marker);
  await page.getByRole("button", { name: "搜索源码" }).hover();
  await page.getByRole("button", { name: "搜索源码" }).click();

  await expect(page.getByText("未找到匹配的源码文件或内容")).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByText("搜索结果 (0)")).toBeVisible();
  await expect(page.getByText("linked-secret.txt")).toHaveCount(0);
  await expect(page.locator("pre")).toHaveCount(0);
});
