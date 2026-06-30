import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
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

  test("prevents duplicate workspace creation from a real double click", async ({
    page,
    request,
  }) => {
    const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-double-workspace-")));
    fs.writeFileSync(path.join(repo, "README.md"), "double workspace creation e2e\n", "utf8");
    const workspaceName = `double-workspace-${Date.now()}`;

    await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
    await page.getByPlaceholder(/项目 A/).fill(workspaceName);
    await page.getByPlaceholder(/本地文件夹路径/).fill(repo);

    const createRequests: string[] = [];
    page.on("request", (req) => {
      if (req.method() === "POST" && new URL(req.url()).pathname === "/api/workspaces") {
        createRequests.push(req.url());
      }
    });
    const firstCreate = page.waitForRequest(
      (req) => req.method() === "POST" && new URL(req.url()).pathname === "/api/workspaces",
    );

    const createButton = page.getByRole("button", { name: "创建工作空间" });
    await createButton.hover();
    await createButton.dblclick();
    await firstCreate;

    await page.waitForURL(/\/workspaces\/[0-9a-f-]{36}$/, { timeout: 30_000 });
    await expect(page.getByText(workspaceName)).toBeVisible({ timeout: 30_000 });
    await expect.poll(() => createRequests.length).toBe(1);

    const listResp = await request.get(`${backendBase}/api/workspaces`);
    expect(listResp.ok()).toBeTruthy();
    const workspaces = (await listResp.json()) as Array<{ name: string; repo_path: string }>;
    const created = workspaces.filter((item) => item.name === workspaceName);
    expect(created).toHaveLength(1);
    expect(created[0].repo_path).toBe(repo);
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

  test("prevents duplicate material uploads from a real double click", async ({
    page,
    request,
  }) => {
    const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-double-material-")));
    fs.writeFileSync(path.join(repo, "README.md"), "double material upload e2e\n", "utf8");
    const materialPath = path.join(repo, "requirements.md");
    fs.writeFileSync(materialPath, "# Requirements\n\nOnly one copy should enter context.\n", "utf8");
    const workspaceName = `double-material-${Date.now()}`;

    await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
    await page.getByPlaceholder(/项目 A/).fill(workspaceName);
    await page.getByPlaceholder(/本地文件夹路径/).fill(repo);
    await page.getByRole("button", { name: "创建工作空间" }).hover();
    await page.getByRole("button", { name: "创建工作空间" }).click();
    await page.waitForURL(/\/workspaces\/[0-9a-f-]{36}$/, { timeout: 30_000 });
    const workspaceId = page.url().split("/").pop() ?? "";
    await expect(page.getByText(workspaceName)).toBeVisible({ timeout: 30_000 });

    await page.getByRole("button", { name: /材料 \(0\)/ }).hover();
    await page.getByRole("button", { name: /材料 \(0\)/ }).click();
    await page.getByPlaceholder(/输入文件绝对路径/).fill(materialPath);

    const uploadRequests: string[] = [];
    page.on("request", (req) => {
      if (
        req.method() === "POST" &&
        new URL(req.url()).pathname === `/api/workspaces/${workspaceId}/materials`
      ) {
        uploadRequests.push(req.url());
      }
    });
    const firstUpload = page.waitForRequest(
      (req) =>
        req.method() === "POST" &&
        new URL(req.url()).pathname === `/api/workspaces/${workspaceId}/materials`,
    );

    await page.getByRole("button", { name: "添加" }).hover();
    await page.getByRole("button", { name: "添加" }).dblclick();
    await firstUpload;

    await expect(page.getByRole("button", { name: /材料 \(1\)/ })).toBeVisible({
      timeout: 15_000,
    });
    await expect.poll(() => uploadRequests.length).toBe(1);

    const workspaceResp = await request.get(`${backendBase}/api/workspaces/${workspaceId}`);
    expect(workspaceResp.ok()).toBeTruthy();
    const workspace = (await workspaceResp.json()) as { materials: Array<{ filename: string }> };
    expect(workspace.materials.filter((item) => item.filename === "requirements.md")).toHaveLength(1);
  });

  test("workspace AI thread bridge opens a scoped investigation thread through the UI", async ({
    page,
    request,
  }) => {
    test.setTimeout(60_000);
    const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-bridge-")));
    fs.writeFileSync(path.join(repo, "README.md"), "workspace AI thread bridge e2e\n", "utf8");
    const workspaceName = `ai-bridge-${Date.now()}`;

    await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
    await page.getByPlaceholder(/项目 A/).fill(workspaceName);
    await page.getByPlaceholder(/本地文件夹路径/).fill(repo);
    await page.getByRole("button", { name: "创建工作空间" }).hover();
    await page.getByRole("button", { name: "创建工作空间" }).click();
    await page.waitForURL(/\/workspaces\/[0-9a-f-]{36}$/, { timeout: 30_000 });
    const workspaceId = page.url().split("/").pop() ?? "";
    await expect(page.getByText(workspaceName)).toBeVisible({ timeout: 30_000 });

    await page.getByRole("button", { name: /AI线程/ }).hover();
    await page.getByRole("button", { name: /AI线程/ }).click();
    await expect(page.getByText("在宽屏 AI 线程中继续分析")).toBeVisible();
    const openThread = page.getByRole("button", { name: "打开工作空间 AI 线程" });
    await openThread.hover();
    await openThread.click();
    await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
    const threadId = page.url().split("/").pop() ?? "";
    await expect(page.getByRole("heading", { name: `${workspaceName} · AI 调查线程` })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText(`workspace / ${workspaceId}`)).toBeVisible();
    await expect(page.getByText(`workspace:${workspaceId}`)).toBeVisible();
    await expect(page.getByText(repo)).toBeVisible();

    const threadResp = await request.get(`${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}`);
    expect(threadResp.ok()).toBeTruthy();
    const thread = (await threadResp.json()) as {
      scope_type: string;
      scope_id: string;
      workspace_id: string;
      memory_namespace: string;
      initial_context: { repo_path?: string; completed_reports?: number };
    };
    expect(thread.scope_type).toBe("workspace");
    expect(thread.scope_id).toBe(workspaceId);
    expect(thread.workspace_id).toBe(workspaceId);
    expect(thread.memory_namespace).toBe(`workspace:${workspaceId}`);
    expect(thread.initial_context.repo_path).toBe(repo);
    expect(thread.initial_context.completed_reports).toBe(0);
  });

  test("workspace report export download redacts completed report secrets through the UI", async ({
    page,
  }, testInfo) => {
    test.setTimeout(60_000);
    test.skip(!process.env.CODETALK_PLAYWRIGHT_SQLITE_DB, "requires explicit Playwright sqlite path");
    const sqliteDb = process.env.CODETALK_PLAYWRIGHT_SQLITE_DB!;
    const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-report-export-")));
    fs.writeFileSync(path.join(repo, "README.md"), "workspace report export redaction e2e\n", "utf8");
    const workspaceName = `report-export-redact-${Date.now()}`;
    const reportSecret = ["sk", "workspaceUiReportLeakValue1234567890"].join("-");
    const tokenSecret = "workspaceUiReportTokenLeakValue1234567890";
    const bearerSecret = "workspaceUiReportBearerLeakValue1234567890";

    await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
    await page.getByPlaceholder(/项目 A/).fill(workspaceName);
    await page.getByPlaceholder(/本地文件夹路径/).fill(repo);
    await page.getByRole("button", { name: "创建工作空间" }).hover();
    await page.getByRole("button", { name: "创建工作空间" }).click();
    await page.waitForURL(/\/workspaces\/[0-9a-f-]{36}$/, { timeout: 30_000 });
    const workspaceId = page.url().split("/").pop() ?? "";
    await expect(page.getByText(workspaceName)).toBeVisible({ timeout: 30_000 });

    execFileSync(
      "python3",
      [
        "-c",
        [
          "import sqlite3, sys, uuid",
          "db, ws, report_secret, token_secret, bearer_secret = sys.argv[1:]",
          "conn = sqlite3.connect(db)",
          "conn.execute(",
          "  'INSERT INTO workspace_reports (id, workspace_id, report_type, title, content, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',",
          "  (str(uuid.uuid4()), ws, 'analysis', 'redacted-report.md', '\\n'.join(['# Report', 'workspace report export complete', f'model key: {report_secret}', 'runtime ' + 'tok' + f'en={token_secret}', 'Authorization:' + f' Bearer {bearer_secret}']), 'completed', '2025-06-01T10:00:00')",
          ")",
          "conn.commit()",
          "conn.close()",
        ].join("\n"),
        sqliteDb,
        workspaceId,
        reportSecret,
        tokenSecret,
        bearerSecret,
      ],
      { stdio: "pipe" },
    );

    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.getByRole("button", { name: /报告 \(1\)/ })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("redacted-report.md")).toBeVisible();

    const downloadPromise = page.waitForEvent("download");
    const mdExportButton = page.getByRole("button", { name: /^md$/i });
    await mdExportButton.hover();
    await mdExportButton.click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/^workspace-.*\.zip$/);
    const zipPath = testInfo.outputPath("workspace-report-redacted-export.zip");
    await download.saveAs(zipPath);
    const exported = execFileSync(
      "python3",
      [
        "-c",
        [
          "import sys, zipfile",
          "with zipfile.ZipFile(sys.argv[1]) as zf:",
          "    print(zf.read('redacted-report.md').decode('utf-8'))",
        ].join("\n"),
        zipPath,
      ],
      { encoding: "utf8" },
    );

    expect(exported).toContain("workspace report export complete");
    expect(exported).toContain("<redacted>");
    expect(exported).not.toContain(reportSecret);
    expect(exported).not.toContain(tokenSecret);
    expect(exported).not.toContain(bearerSecret);
    expect(exported).not.toMatch(/sk-[A-Za-z0-9_-]{12,}/);
    expect(exported).not.toMatch(/Authorization:\s*Bearer\s+(?!<redacted>)[^\s"']+/i);
    expect(exported).not.toMatch(/(?:api[-_]?key|token|secret|password)=['"]?(?!<redacted>)[^\s"']+/i);
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

  test("prevents duplicate workspace reindex requests from a real double click", async ({
    page,
    request,
  }) => {
    const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-double-reindex-")));
    fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
    fs.writeFileSync(path.join(repo, "README.md"), "double reindex workspace e2e\n", "utf8");
    fs.writeFileSync(
      path.join(repo, "lib", "nvmf", "double_reindex.c"),
      "int codetalk_double_reindex_probe(void) { return 11; }\n",
      "utf8",
    );
    const workspaceName = `double-reindex-${Date.now()}`;

    await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
    await page.getByPlaceholder(/项目 A/).fill(workspaceName);
    await page.getByPlaceholder(/本地文件夹路径/).fill(repo);
    await page.getByRole("button", { name: "创建工作空间" }).hover();
    await page.getByRole("button", { name: "创建工作空间" }).click();
    await page.waitForURL(/\/workspaces\/[0-9a-f-]{36}$/, { timeout: 30_000 });
    const workspaceId = page.url().split("/").pop() ?? "";
    await expect(page.getByText(workspaceName)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("已索引")).toBeVisible({ timeout: 120_000 });

    const reindexRequests: string[] = [];
    page.on("request", (req) => {
      if (
        req.method() === "POST" &&
        new URL(req.url()).pathname === `/api/workspaces/${workspaceId}/reindex`
      ) {
        reindexRequests.push(req.url());
      }
    });
    const firstReindex = page.waitForRequest(
      (req) =>
        req.method() === "POST" &&
        new URL(req.url()).pathname === `/api/workspaces/${workspaceId}/reindex`,
    );

    const reindexButton = page.getByRole("button", { name: "重新索引" });
    await reindexButton.hover();
    await reindexButton.dblclick();
    await firstReindex;

    await expect(page.getByText(/索引中/)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("已索引")).toBeVisible({ timeout: 120_000 });
    await expect.poll(() => reindexRequests.length).toBe(1);

    const workspaceResp = await request.get(`${backendBase}/api/workspaces/${workspaceId}`);
    expect(workspaceResp.ok()).toBeTruthy();
    const workspace = (await workspaceResp.json()) as { indexed: number; repo_path: string };
    expect(workspace.indexed).toBe(1);
    expect(workspace.repo_path).toBe(repo);
  });

  test("prevents duplicate workspace analysis starts from a real double click", async ({
    page,
    request,
  }) => {
    test.setTimeout(60_000);
    const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-double-analysis-")));
    fs.mkdirSync(path.join(repo, "lib", "bdev"), { recursive: true });
    fs.writeFileSync(path.join(repo, "README.md"), "double analysis start e2e\n", "utf8");
    fs.writeFileSync(
      path.join(repo, "lib", "bdev", "double_analysis.c"),
      "int codetalk_double_analysis_probe(void) { return 17; }\n",
      "utf8",
    );
    const workspaceName = `double-analysis-${Date.now()}`;

    await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
    await page.getByPlaceholder(/项目 A/).fill(workspaceName);
    await page.getByPlaceholder(/本地文件夹路径/).fill(repo);
    await page.getByRole("button", { name: "创建工作空间" }).hover();
    await page.getByRole("button", { name: "创建工作空间" }).click();
    await page.waitForURL(/\/workspaces\/[0-9a-f-]{36}$/, { timeout: 30_000 });
    const workspaceId = page.url().split("/").pop() ?? "";
    await expect(page.getByText(workspaceName)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("已索引")).toBeVisible({ timeout: 120_000 });

    await page.getByRole("button", { name: "生成报告" }).hover();
    await page.getByRole("button", { name: "生成报告" }).click();
    await expect(page.getByRole("heading", { name: "生成测试视角报告 · 分析任务" })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByRole("button", { name: "启动分析" })).toBeEnabled({
      timeout: 15_000,
    });

    const analyzeRequests: string[] = [];
    page.on("request", (req) => {
      if (
        req.method() === "POST" &&
        new URL(req.url()).pathname === `/api/workspaces/${workspaceId}/analyze`
      ) {
        analyzeRequests.push(req.url());
      }
    });
    const firstAnalyze = page.waitForRequest(
      (req) =>
        req.method() === "POST" &&
        new URL(req.url()).pathname === `/api/workspaces/${workspaceId}/analyze`,
    );

    const startButton = page.getByRole("button", { name: "启动分析" });
    await startButton.hover();
    await startButton.dblclick();
    await firstAnalyze;

    await expect(page.getByRole("heading", { name: "生成测试视角报告 · 分析任务" })).toHaveCount(0, {
      timeout: 15_000,
    });
    await expect.poll(() => analyzeRequests.length).toBe(1);

    const statusResp = await request.get(`${backendBase}/api/workspaces/${workspaceId}/analyze-status`);
    expect(statusResp.ok()).toBeTruthy();
    const status = (await statusResp.json()) as { analyze_status: string | null };
    expect(["running", "completed", "failed"].includes(status.analyze_status ?? "")).toBeTruthy();
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
