import fs from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

const enabled = process.env.CODETALK_E2E_REAL_BUILTIN_B === "1";
const apiKey = process.env.CODETALK_E2E_LLM_API_KEY ?? "";
const repoPath = process.env.CODETALK_E2E_REPO ?? "/Volumes/Media/dpdk/spdk";
const dataDir = process.env.CODETALK_PLAYWRIGHT_DATA_DIR ?? "";
const executionProfile = process.env.CODETALK_E2E_EXECUTION_PROFILE ?? "rapid";
const profileLabel = executionProfile === "deep" ? "深度型" : "速度型";
const profileMaximumDurationMs = executionProfile === "deep" ? 95 * 60_000 : 25 * 60_000;

test.skip(!enabled, "Set CODETALK_E2E_REAL_BUILTIN_B=1 to run the real built-in model acceptance flow");
test.skip(!apiKey, "CODETALK_E2E_LLM_API_KEY is required for the real built-in model acceptance flow");
test.skip(!fs.existsSync(repoPath), `SPDK repository is unavailable: ${repoPath}`);
test.skip(!dataDir, "CODETALK_PLAYWRIGHT_DATA_DIR is required to retain real-run evidence");

test("V3 basic source plus design workflow runs through the browser with a real built-in model", async ({ page }) => {
  test.setTimeout(profileMaximumDurationMs + 5 * 60_000);
  const stamp = Date.now();
  const workspaceName = `SPDK Builtin B ${stamp}`;
  const taskName = `iSCSI login Builtin B ${stamp}`;
  const configName = `DeepSeek Builtin B ${stamp}`;
  const auditConfigName = `DeepSeek Audit B ${stamp}`;
  const designDocument = path.join(process.cwd(), "e2e/fixtures/v3-iscsi-login-design.md");
  const target = [
    "分析 SPDK iSCSI login 的主流程、认证与协商异常、命令和连接资源、并发与恢复测试设计。",
    "所有结论必须基于真实源码和设计文档；输出流程、SFMEA、外部可执行黑盒测试用例与完整报告。",
  ].join("\n");

  await page.setViewportSize({ width: 1440, height: 900 });
  await configureBuiltInModelsThroughUi(page, configName, auditConfigName);
  await createWorkspaceThroughUi(page, workspaceName);
  const runId = await createAndRunTaskThroughUi(page, { taskName, workspaceName, target, designDocument });

  const startedAt = Date.now();
  const status = page.locator(".ct-v2-run-status").filter({ hasText: "执行状态" }).locator("strong");
  await expect.poll(async () => (await status.textContent())?.trim(), {
    timeout: profileMaximumDurationMs,
    intervals: [1_000, 2_000, 5_000, 10_000],
  }).toMatch(/^(已完成|部分完成|失败|已阻断|已取消)$/);
  const elapsedMs = Date.now() - startedAt;
  const terminalStatus = (await status.textContent())?.trim() || "";
  expect(terminalStatus).toMatch(/^(已完成|部分完成)$/);

  await expect(page.locator(".ct-v2-run-deliverables")).toBeVisible({ timeout: 30_000 });
  const quality = page.locator(".ct-v2-run-status").filter({ hasText: "质量状态" }).locator("strong");
  await expect(quality).toHaveText("通过");
  await page.screenshot({ path: path.join(dataDir, "v3-basic-builtin-b-completed.png"), fullPage: false });

  const runRoot = path.join(dataDir, "workbench", "task_runs", runId);
  const manifestPath = path.join(runRoot, "task_artifact_manifest.json");
  expect(fs.existsSync(manifestPath)).toBeTruthy();
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8")) as { artifacts?: Array<{ name?: string; path?: string }> };
  const artifactNames = (manifest.artifacts ?? []).map((item) =>
    path.basename(String(item.name ?? item.path ?? "")),
  );
  for (const required of ["source_analysis.md", "source_scope.json", "evidence_cards.json"]) {
    expect(artifactNames).toContain(required);
  }

  fs.writeFileSync(
    path.join(dataDir, "v3-basic-builtin-b-metrics.json"),
    JSON.stringify({ run_id: runId, repo_path: repoPath, elapsed_ms: elapsedMs, artifact_names: artifactNames }, null, 2),
    "utf8",
  );
  expect(elapsedMs).toBeGreaterThanOrEqual(60_000);
  expect(elapsedMs).toBeLessThanOrEqual(profileMaximumDurationMs);
});

async function configureBuiltInModelsThroughUi(
  page: import("@playwright/test").Page,
  configName: string,
  auditConfigName: string,
) {
  await page.goto("/settings", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /可选：内置模型与 RAG 检索/ }).click();
  await createModelConfigThroughUi(page, configName, process.env.CODETALK_E2E_LLM_MODEL ?? "deepseek-chat");
  await createModelConfigThroughUi(page, auditConfigName, process.env.CODETALK_E2E_AUDIT_LLM_MODEL ?? "deepseek-reasoner");
  await page.getByLabel("独立质量核验模型").selectOption({ label: `${auditConfigName} (${process.env.CODETALK_E2E_AUDIT_LLM_MODEL ?? "deepseek-reasoner"})` });
  await expect(page.getByLabel("独立质量核验模型")).toHaveValue(/.+/);
  await expect(page.locator("body")).not.toContainText(apiKey);
}

async function createModelConfigThroughUi(
  page: import("@playwright/test").Page,
  configName: string,
  model: string,
) {
  await page.getByRole("button", { name: "新增" }).click();
  const form = page.locator("form").filter({ hasText: "新增 LLM 配置" });
  await form.getByPlaceholder("如：Claude / GPT-4o").fill(configName);
  await form.getByPlaceholder("https://api.openai.com/v1").fill(process.env.CODETALK_E2E_LLM_BASE_URL ?? "https://api.deepseek.com");
  await form.getByPlaceholder(/sk-|Ollama/).fill(apiKey);
  await form.getByPlaceholder(/gpt-4o|text-embedding/).fill(model);
  await form.getByRole("button", { name: "测试连接" }).hover();
  await form.getByRole("button", { name: "测试连接" }).click();
  await expect(form).toContainText(/连接成功|测试成功|模型响应正常/, { timeout: 90_000 });
  await form.getByRole("button", { name: "保存配置" }).click();
  await expect(page.getByText(configName, { exact: true })).toBeVisible({ timeout: 15_000 });
}

async function createWorkspaceThroughUi(page: import("@playwright/test").Page, workspaceName: string) {
  await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
  await page.getByLabel("工作空间名称").fill(workspaceName);
  await page.getByLabel("代码仓库路径").fill(repoPath);
  await page.getByRole("button", { name: "创建工作空间" }).hover();
  await page.getByRole("button", { name: "创建工作空间" }).click();
  await page.waitForURL(/\/workspaces\/[0-9a-f-]{36}$/, { timeout: 90_000 });
}

async function createAndRunTaskThroughUi(
  page: import("@playwright/test").Page,
  values: { taskName: string; workspaceName: string; target: string; designDocument: string },
) {
  await page.goto("/tasks/new", { waitUntil: "domcontentloaded" });
  await page.getByRole("radio", { name: /基础源码 \+ 设计文档报告（内置模型）/ }).check();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByRole("textbox", { name: "任务名称 *" }).fill(values.taskName);
  await page.getByLabel("工作空间 *").selectOption({ label: values.workspaceName });
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByRole("textbox", { name: "分析目标 *" }).fill(values.target);
  await page.locator('input[type="file"]').setInputFiles(values.designDocument);
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.getByRole("heading", { name: "确认执行配置" })).toBeVisible();
  const selectedProfile = page.getByRole("radio", { name: new RegExp(profileLabel) });
  await selectedProfile.check();
  await expect(selectedProfile).toBeChecked();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.getByRole("heading", { name: "确认交付输出" })).toBeVisible();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.getByRole("heading", { name: "检查并运行" })).toBeVisible();
  await expect(page.getByText("执行档位", { exact: true }).locator(".."))
    .toContainText(profileLabel);
  await page.getByRole("button", { name: "保存并运行" }).hover();
  await page.getByRole("button", { name: "保存并运行" }).click();
  await expect(page).toHaveURL(/\/tasks\/task_[^/]+\/runs\/task_run_/);
  const runId = page.url().split("/").pop() ?? "";
  expect(runId).toMatch(/^task_run_/);
  return runId;
}
