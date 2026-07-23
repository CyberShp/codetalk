import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const enabled = process.env.CODETALK_E2E_REAL_CODEX === "1";
const repoPath = process.env.CODETALK_E2E_REPO ?? "/Volumes/Media/dpdk/spdk";
const dataDir = process.env.CODETALK_PLAYWRIGHT_DATA_DIR ?? "";

test.skip(!enabled, "Set CODETALK_E2E_REAL_CODEX=1 to run the real Codex CLI acceptance flow");
test.skip(!fs.existsSync(repoPath), `SPDK repository is unavailable: ${repoPath}`);
test.skip(!dataDir, "CODETALK_PLAYWRIGHT_DATA_DIR is required to retain real-run evidence");

test("V3 basic source report runs through the browser with a real Codex CLI", async ({ page }) => {
  test.setTimeout(35 * 60_000);
  const stamp = Date.now();
  const workspaceName = `SPDK Codex V3 ${stamp}`;
  const taskName = `iSCSI login V3 ${stamp}`;

  await page.setViewportSize({ width: 1440, height: 900 });
  await ensureCodexRuntimeIsReady(page);
  await createWorkspaceThroughUi(page, workspaceName);
  const runId = await createAndRunTaskThroughUi(page, { workspaceName, taskName });

  const startedAt = Date.now();
  const status = page.locator(".ct-v2-run-status").filter({ hasText: "执行状态" }).locator("strong");
  await expect.poll(async () => (await status.textContent())?.trim(), {
    timeout: 30 * 60_000,
    intervals: [1000, 2000, 5000, 10_000],
  }).toMatch(/^(已完成|部分完成)$/);
  const elapsedMs = Date.now() - startedAt;

  await expect(page.getByText(/交付文件|正式交付件/).first()).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: path.join(dataDir, "v3-basic-codex-completed.png"), fullPage: false });

  const runRoot = path.join(dataDir, "workbench", "task_runs", runId);
  const manifestPath = path.join(runRoot, "task_artifact_manifest.json");
  expect(fs.existsSync(manifestPath)).toBeTruthy();
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8")) as { artifacts?: Array<{ name?: string; path?: string }> };
  const artifactNames = (manifest.artifacts ?? []).map((item) => item.name ?? item.path ?? "");
  expect(artifactNames.some((name) => /report\.md|分析报告/.test(name))).toBeTruthy();

  fs.writeFileSync(
    path.join(dataDir, "v3-basic-codex-metrics.json"),
    JSON.stringify({ run_id: runId, repo_path: repoPath, elapsed_ms: elapsedMs, artifact_names: artifactNames }, null, 2),
    "utf8",
  );
  expect(elapsedMs).toBeGreaterThanOrEqual(60_000);
  expect(elapsedMs).toBeLessThanOrEqual(30 * 60_000);
});

async function ensureCodexRuntimeIsReady(page: import("@playwright/test").Page) {
  await page.goto("/settings", { waitUntil: "domcontentloaded" });
  const runtime = page.getByTestId("agent-runtime-card-default-codex");
  await expect(runtime).toBeVisible({ timeout: 30_000 });
  await runtime.getByRole("button", { name: "测试" }).hover();
  await runtime.getByRole("button", { name: "测试" }).click();
  await expect(runtime).toContainText(/可用/, { timeout: 90_000 });
}

async function createWorkspaceThroughUi(page: import("@playwright/test").Page, workspaceName: string) {
  await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
  await page.getByPlaceholder(/项目 A/).fill(workspaceName);
  await page.getByPlaceholder(/本地文件夹路径/).fill(repoPath);
  const create = page.getByRole("button", { name: "创建工作空间" });
  await create.hover();
  await create.click();
  await page.waitForURL(/\/workspaces\/[0-9a-f-]{36}$/, { timeout: 90_000 });
  await expect(page.getByText(workspaceName, { exact: true })).toBeVisible();
}

async function createAndRunTaskThroughUi(
  page: import("@playwright/test").Page,
  values: { workspaceName: string; taskName: string },
) {
  await page.goto("/tasks/new", { waitUntil: "domcontentloaded" });
  const workflow = page.locator(".ct-v2-workflow-choice label").filter({ hasText: "基础源码报告（Codex CLI）" });
  await expect(workflow).toBeVisible({ timeout: 30_000 });
  await workflow.getByRole("radio").check();
  await page.getByRole("button", { name: "保存并继续" }).click();

  await page.getByRole("textbox", { name: "任务名称 *" }).fill(values.taskName);
  await page.getByLabel("工作空间 *").selectOption({ label: values.workspaceName });
  await page.getByRole("textbox", { name: "描述" }).fill("真实 Codex CLI 的 SPDK iSCSI login 源码驱动测试交付");
  await page.getByRole("button", { name: "保存并继续" }).click();

  await expect(page.getByRole("heading", { name: "填写本次输入" })).toBeVisible();
  await expect(page.getByText("无需额外输入")).toBeVisible();
  await page.getByRole("button", { name: "保存并继续" }).click();

  await expect(page.getByRole("heading", { name: "确认执行配置" })).toBeVisible();
  await expect(page.getByText(/默认完整继承工作流/)).toBeVisible();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.getByRole("heading", { name: "确认交付输出" })).toBeVisible();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.getByRole("heading", { name: "检查并运行" })).toBeVisible();
  await page.getByRole("button", { name: "保存并运行" }).hover();
  await page.getByRole("button", { name: "保存并运行" }).click();
  await expect(page).toHaveURL(/\/tasks\/task_[^/]+\/runs\/task_run_/);
  const runId = page.url().split("/").pop() ?? "";
  expect(runId).toMatch(/^task_run_/);
  return runId;
}
