import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

test("task export download redacts completed markdown report secrets through the UI", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(60_000);
  test.skip(!process.env.CODETALK_PLAYWRIGHT_DATA_DIR, "requires explicit Playwright data dir");
  const dataDir = process.env.CODETALK_PLAYWRIGHT_DATA_DIR!;
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-task-export-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "task export redaction e2e\n", "utf8");
  const reportSecret = ["sk", "taskUiReportLeakValue1234567890"].join("-");
  const tokenSecret = "taskUiReportTokenLeakValue1234567890";
  const bearerSecret = "taskUiReportBearerLeakValue1234567890";

  const createResp = await request.post(`${backendBase}/api/tasks`, {
    data: {
      name: `task-export-redact-${Date.now()}`,
      repo_path: repo,
      tools: [],
      analysis_focus: "Task export redaction",
      prompt_content: "Prepare a task whose generated report must be safe to export.",
    },
  });
  expect(createResp.status()).toBe(201);
  const task = (await createResp.json()) as { id: string };

  execFileSync(
    "python3",
    [
      "-c",
      [
        "import pathlib, sys",
        "data_dir, task_id, report_secret, token_secret, bearer_secret = sys.argv[1:]",
        "output_dir = pathlib.Path(data_dir) / 'outputs' / task_id",
        "output_dir.mkdir(parents=True, exist_ok=True)",
        "content = '\\n'.join(['# Task Report', 'task ui export complete', f'model key: {report_secret}', 'runtime ' + 'tok' + f'en={token_secret}', 'Authorization:' + f' Bearer {bearer_secret}'])",
        "(output_dir / 'task-redacted-report.md').write_text(content, encoding='utf-8')",
      ].join("\n"),
      dataDir,
      task.id,
      reportSecret,
      tokenSecret,
      bearerSecret,
    ],
    { stdio: "pipe" },
  );

  await page.goto(`/tasks/${task.id}/export`, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "导出结果" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Markdown 适用于 GitHub / 知识库" })).toBeVisible();

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: /下载 Markdown 文件/ }).hover();
  await page.getByRole("button", { name: /下载 Markdown 文件/ }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^codetalk-.*\.zip$/);
  const zipPath = testInfo.outputPath("task-report-redacted-export.zip");
  await download.saveAs(zipPath);
  const exported = execFileSync(
    "python3",
    [
      "-c",
      [
        "import sys, zipfile",
        "with zipfile.ZipFile(sys.argv[1]) as zf:",
        "    print(zf.read('task-redacted-report.md').decode('utf-8'))",
      ].join("\n"),
      zipPath,
    ],
    { encoding: "utf8" },
  );

  expect(exported).toContain("task ui export complete");
  expect(exported).toContain("<redacted>");
  expect(exported).not.toContain(reportSecret);
  expect(exported).not.toContain(tokenSecret);
  expect(exported).not.toContain(bearerSecret);
  expect(exported).not.toMatch(/sk-[A-Za-z0-9_-]{12,}/);
  expect(exported).not.toMatch(/Authorization:\s*Bearer\s+(?!<redacted>)[^\s"']+/i);
  expect(exported).not.toMatch(/(?:api[-_]?key|token|secret|password)=['"]?(?!<redacted>)[^\s"']+/i);
});

test("task export download redacts structured JSON and YAML secrets through the UI", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(60_000);
  test.skip(!process.env.CODETALK_PLAYWRIGHT_DATA_DIR, "requires explicit Playwright data dir");
  const dataDir = process.env.CODETALK_PLAYWRIGHT_DATA_DIR!;
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-task-structured-export-")));
  fs.writeFileSync(path.join(repo, "README.md"), "task structured export redaction e2e\n", "utf8");
  const jsonSecret = "taskUiStructuredJsonTokenLeakValue1234567890";
  const yamlSecret = "taskUiStructuredYamlSecretLeakValue1234567890";

  const createResp = await request.post(`${backendBase}/api/tasks`, {
    data: {
      name: `task-export-structured-${Date.now()}`,
      repo_path: repo,
      tools: [],
      analysis_focus: "Task structured export redaction",
      prompt_content: "Prepare a task whose structured diagnostics must be safe to export.",
    },
  });
  expect(createResp.status()).toBe(201);
  const task = (await createResp.json()) as { id: string };

  execFileSync(
    "python3",
    [
      "-c",
      [
        "import json, pathlib, sys",
        "data_dir, task_id, json_secret, yaml_secret = sys.argv[1:]",
        "output_dir = pathlib.Path(data_dir) / 'outputs' / task_id",
        "output_dir.mkdir(parents=True, exist_ok=True)",
        "content = '\\n'.join(['# Structured Task Report', 'task ui structured export complete', json.dumps({'access_token': json_secret}), f'secret: {yaml_secret}'])",
        "(output_dir / 'task-structured-redacted-report.md').write_text(content, encoding='utf-8')",
      ].join("\n"),
      dataDir,
      task.id,
      jsonSecret,
      yamlSecret,
    ],
    { stdio: "pipe" },
  );

  await page.goto(`/tasks/${task.id}/export`, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "导出结果" })).toBeVisible();

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: /下载 Markdown 文件/ }).hover();
  await page.getByRole("button", { name: /下载 Markdown 文件/ }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^codetalk-.*\.zip$/);
  const zipPath = testInfo.outputPath("task-structured-report-redacted-export.zip");
  await download.saveAs(zipPath);
  const exported = execFileSync(
    "python3",
    [
      "-c",
      [
        "import sys, zipfile",
        "with zipfile.ZipFile(sys.argv[1]) as zf:",
        "    print(zf.read('task-structured-redacted-report.md').decode('utf-8'))",
      ].join("\n"),
      zipPath,
    ],
    { encoding: "utf8" },
  );

  expect(exported).toContain("task ui structured export complete");
  expect(exported).toContain('"access_token": "<redacted>"');
  expect(exported).toContain("secret: <redacted>");
  expect(exported).not.toContain(jsonSecret);
  expect(exported).not.toContain(yamlSecret);
});

test("task export format selection downloads redacted XML through the UI", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(60_000);
  test.skip(!process.env.CODETALK_PLAYWRIGHT_DATA_DIR, "requires explicit Playwright data dir");
  const dataDir = process.env.CODETALK_PLAYWRIGHT_DATA_DIR!;
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-task-xml-export-")));
  fs.writeFileSync(path.join(repo, "README.md"), "task xml export redaction e2e\n", "utf8");
  const reportSecret = ["sk", "taskXmlExportLeakValue1234567890"].join("-");
  const tokenSecret = "taskXmlExportTokenLeakValue1234567890";
  const bearerSecret = "taskXmlExportBearerLeakValue1234567890";

  const createResp = await request.post(`${backendBase}/api/tasks`, {
    data: {
      name: `task-export-xml-${Date.now()}`,
      repo_path: repo,
      tools: [],
      analysis_focus: "Task XML export redaction",
      prompt_content: "Prepare a task whose XML export must be safe.",
    },
  });
  expect(createResp.status()).toBe(201);
  const task = (await createResp.json()) as { id: string };

  execFileSync(
    "python3",
    [
      "-c",
      [
        "import pathlib, sys",
        "data_dir, task_id, report_secret, token_secret, bearer_secret = sys.argv[1:]",
        "output_dir = pathlib.Path(data_dir) / 'outputs' / task_id",
        "output_dir.mkdir(parents=True, exist_ok=True)",
        "content = '\\n'.join(['# XML Task Report', 'task ui xml export complete', f'model key: {report_secret}', 'runtime ' + 'tok' + f'en={token_secret}', 'Authorization:' + f' Bearer {bearer_secret}'])",
        "(output_dir / 'xml-safe-report.md').write_text(content, encoding='utf-8')",
      ].join("\n"),
      dataDir,
      task.id,
      reportSecret,
      tokenSecret,
      bearerSecret,
    ],
    { stdio: "pipe" },
  );

  await page.goto(`/tasks/${task.id}/export`, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "导出结果" })).toBeVisible();
  await page.getByRole("button", { name: "XML 适用于系统集成 / 数据交换" }).hover();
  await page.getByRole("button", { name: "XML 适用于系统集成 / 数据交换" }).click();
  await expect(page.getByRole("button", { name: /下载 XML 文件/ })).toBeVisible();

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: /下载 XML 文件/ }).hover();
  await page.getByRole("button", { name: /下载 XML 文件/ }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^codetalk-.*\.xml$/);
  const xmlPath = testInfo.outputPath("task_report_redacted_export.xml");
  await download.saveAs(xmlPath);
  const exported = fs.readFileSync(xmlPath, "utf8");

  expect(exported).toContain("<codetalk-reports");
  expect(exported).toContain('filename="xml-safe-report.md"');
  expect(exported).toContain("task ui xml export complete");
  expect(exported).toContain("&lt;redacted&gt;");
  expect(exported).not.toContain(reportSecret);
  expect(exported).not.toContain(tokenSecret);
  expect(exported).not.toContain(bearerSecret);
  expect(exported).not.toMatch(/sk-[A-Za-z0-9_-]{12,}/);
  expect(exported).not.toMatch(/Authorization:\s*Bearer\s+(?!&lt;redacted&gt;)[^\s"'<]+/i);
  expect(exported).not.toMatch(/(?:api[-_]?key|token|secret|password)=['"]?(?!&lt;redacted&gt;)[^\s"'<]+/i);
});

test("task export format selection downloads redacted Word document through the UI", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(60_000);
  test.skip(!process.env.CODETALK_PLAYWRIGHT_DATA_DIR, "requires explicit Playwright data dir");
  const dataDir = process.env.CODETALK_PLAYWRIGHT_DATA_DIR!;
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk_docx_export_")));
  fs.writeFileSync(path.join(repo, "README.md"), "task docx export redaction e2e\n", "utf8");
  const reportSecret = ["sk", "taskDocxExportLeakValue1234567890"].join("-");
  const tokenSecret = "taskDocxExportTokenLeakValue1234567890";
  const bearerSecret = "taskDocxExportBearerLeakValue1234567890";

  const createResp = await request.post(`${backendBase}/api/tasks`, {
    data: {
      name: `docx_export_${Date.now()}`,
      repo_path: repo,
      tools: [],
      analysis_focus: "Task DOCX export redaction",
      prompt_content: "Prepare a task whose Word export must be safe.",
    },
  });
  expect(createResp.status()).toBe(201);
  const task = (await createResp.json()) as { id: string };

  execFileSync(
    "python3",
    [
      "-c",
      [
        "import pathlib, sys",
        "data_dir, task_id, report_secret, token_secret, bearer_secret = sys.argv[1:]",
        "output_dir = pathlib.Path(data_dir) / 'outputs' / task_id",
        "output_dir.mkdir(parents=True, exist_ok=True)",
        "content = '\\n'.join(['# DOCX Task Report', 'task ui docx export complete', f'model key: {report_secret}', 'runtime ' + 'tok' + f'en={token_secret}', 'Authorization:' + f' Bearer {bearer_secret}'])",
        "(output_dir / 'docx-safe-report.md').write_text(content, encoding='utf-8')",
      ].join("\n"),
      dataDir,
      task.id,
      reportSecret,
      tokenSecret,
      bearerSecret,
    ],
    { stdio: "pipe" },
  );

  await page.goto(`/tasks/${task.id}/export`, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "导出结果" })).toBeVisible();
  await page.getByRole("button", { name: "Word 文档 适用于正式报告提交" }).hover();
  await page.getByRole("button", { name: "Word 文档 适用于正式报告提交" }).click();
  await expect(page.getByRole("button", { name: /下载 Word 文档 文件/ })).toBeVisible();

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: /下载 Word 文档 文件/ }).hover();
  await page.getByRole("button", { name: /下载 Word 文档 文件/ }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^codetalk-.*\.docx$/);
  const docxPath = testInfo.outputPath("task_report_redacted_export.docx");
  await download.saveAs(docxPath);
  const exported = execFileSync(
    "python3",
    [
      "-c",
      [
        "import html, re, sys, zipfile",
        "with zipfile.ZipFile(sys.argv[1]) as zf:",
        "    xml = zf.read('word/document.xml').decode('utf-8')",
        "text = html.unescape(re.sub('<[^>]+>', '', xml))",
        "print(text)",
      ].join("\n"),
      docxPath,
    ],
    { encoding: "utf8" },
  );

  expect(exported).toContain("DOCX Task Report");
  expect(exported).toContain("task ui docx export complete");
  expect(exported).toContain("<redacted>");
  expect(exported).not.toContain(reportSecret);
  expect(exported).not.toContain(tokenSecret);
  expect(exported).not.toContain(bearerSecret);
  expect(exported).not.toMatch(/sk-[A-Za-z0-9_-]{12,}/);
  expect(exported).not.toMatch(/Authorization:\s*Bearer\s+(?!<redacted>)[^\s"']+/i);
  expect(exported).not.toMatch(/(?:api[-_]?key|token|secret|password)=['"]?(?!<redacted>)[^\s"']+/i);
});

test("task export prevents duplicate downloads from a real double click", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(60_000);
  test.skip(!process.env.CODETALK_PLAYWRIGHT_DATA_DIR, "requires explicit Playwright data dir");
  const dataDir = process.env.CODETALK_PLAYWRIGHT_DATA_DIR!;
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk_task_export_double_")));
  fs.writeFileSync(path.join(repo, "README.md"), "task export double click e2e\n", "utf8");

  const createResp = await request.post(`${backendBase}/api/tasks`, {
    data: {
      name: `task_export_double_${Date.now()}`,
      repo_path: repo,
      tools: [],
      analysis_focus: "Task export double click",
      prompt_content: "Prepare a task whose export should not duplicate.",
    },
  });
  expect(createResp.status()).toBe(201);
  const task = (await createResp.json()) as { id: string };

  execFileSync(
    "python3",
    [
      "-c",
      [
        "import pathlib, sys",
        "data_dir, task_id = sys.argv[1:]",
        "output_dir = pathlib.Path(data_dir) / 'outputs' / task_id",
        "output_dir.mkdir(parents=True, exist_ok=True)",
        "(output_dir / 'double-click-safe-report.md').write_text('# Double Export\\ntask export double click complete', encoding='utf-8')",
      ].join("\n"),
      dataDir,
      task.id,
    ],
    { stdio: "pipe" },
  );

  await page.goto(`/tasks/${task.id}/export`, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "导出结果" })).toBeVisible();

  const downloads: string[] = [];
  page.on("download", (item) => downloads.push(item.suggestedFilename()));

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: /下载 Markdown 文件/ }).hover();
  await page.getByRole("button", { name: /下载 Markdown 文件/ }).dblclick();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^codetalk-.*\.zip$/);
  const zipPath = testInfo.outputPath("task_export_double_click.zip");
  await download.saveAs(zipPath);
  const exported = execFileSync(
    "python3",
    [
      "-c",
      [
        "import sys, zipfile",
        "with zipfile.ZipFile(sys.argv[1]) as zf:",
        "    print(zf.read('double-click-safe-report.md').decode('utf-8'))",
      ].join("\n"),
      zipPath,
    ],
    { encoding: "utf8" },
  );
  expect(exported).toContain("task export double click complete");
  await expect.poll(() => downloads.length).toBe(1);
});

test("task report page redacts persisted markdown report secrets through the UI", async ({
  page,
  request,
}) => {
  test.setTimeout(60_000);
  test.skip(!process.env.CODETALK_PLAYWRIGHT_DATA_DIR, "requires explicit Playwright data dir");
  const dataDir = process.env.CODETALK_PLAYWRIGHT_DATA_DIR!;
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-report-page-")));
  fs.writeFileSync(path.join(repo, "README.md"), "task report page redaction e2e\n", "utf8");
  const reportSecret = ["sk", "taskUiPageLeakValue1234567890"].join("-");
  const tokenSecret = "taskUiPageTokenLeakValue1234567890";
  const bearerSecret = "taskUiPageBearerLeakValue1234567890";

  const createResp = await request.post(`${backendBase}/api/tasks`, {
    data: {
      name: `report-page-redact-${Date.now()}`,
      repo_path: repo,
      tools: [],
      analysis_focus: "Task report page redaction",
      prompt_content: "Prepare a task report whose browser view must be safe.",
    },
  });
  expect(createResp.status()).toBe(201);
  const task = (await createResp.json()) as { id: string };

  execFileSync(
    "python3",
    [
      "-c",
      [
        "import pathlib, sys",
        "data_dir, task_id, report_secret, token_secret, bearer_secret = sys.argv[1:]",
        "output_dir = pathlib.Path(data_dir) / 'outputs' / task_id",
        "output_dir.mkdir(parents=True, exist_ok=True)",
        "content = '\\n'.join(['# Browser Report', 'task ui report page complete', f'model key: {report_secret}', 'runtime ' + 'tok' + f'en={token_secret}', 'Authorization:' + f' Bearer {bearer_secret}'])",
        "(output_dir / 'report-page-redacted-report.md').write_text(content, encoding='utf-8')",
      ].join("\n"),
      dataDir,
      task.id,
      reportSecret,
      tokenSecret,
      bearerSecret,
    ],
    { stdio: "pipe" },
  );

  await page.goto(`/tasks/${task.id}/report`, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "分析报告" })).toBeVisible();
  await expect(page.getByText("task ui report page complete")).toBeVisible();
  await expect(page.locator("body")).toContainText("<redacted>");
  await expect(page.locator("body")).not.toContainText(reportSecret);
  await expect(page.locator("body")).not.toContainText(tokenSecret);
  await expect(page.locator("body")).not.toContainText(bearerSecret);
  await expect(page.locator("body")).not.toContainText(/sk-[A-Za-z0-9_-]{12,}/);
  await expect(page.locator("body")).not.toContainText(/Authorization:\s*Bearer\s+(?!<redacted>)[^\s"']+/i);
  await expect(page.locator("body")).not.toContainText(/(?:api[-_]?key|token|secret|password)=['"]?(?!<redacted>)[^\s"']+/i);
});
