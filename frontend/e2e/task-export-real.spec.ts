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
