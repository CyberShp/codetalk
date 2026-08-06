import { expect, test } from "@playwright/test";
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

assertCanMutatePublicRuntime({ env: process.env, flowName: "V3 restart persistence real E2E" });

const enabled = process.env.CODETALK_E2E_V3_RESTART === "1";
const apiKey = process.env.CODETALK_E2E_LLM_API_KEY ?? "";
const repoPath = process.env.CODETALK_E2E_REPO ?? "/Volumes/Media/dpdk/spdk";
const dataRoot = process.env.CODETALK_PLAYWRIGHT_DATA_DIR ?? "";
const frontendPort = process.env.CODETALK_FRONTEND_PORT ?? "3153";
const designDocument = path.join(process.cwd(), "e2e/fixtures/v3-iscsi-login-design.md");

test.skip(!enabled, "Set CODETALK_E2E_V3_RESTART=1 to run the isolated V3 restart flow");
test.skip(!apiKey, "CODETALK_E2E_LLM_API_KEY is required for the V3 restart flow");
test.skip(!fs.existsSync(repoPath), `SPDK repository is unavailable: ${repoPath}`);
test.skip(!dataRoot, "CODETALK_PLAYWRIGHT_DATA_DIR is required to retain restart evidence");
test.skip(!fs.existsSync(designDocument), `V3 design-document fixture is unavailable: ${designDocument}`);

test("V3 task cockpit restores a real browser-created run after isolated backend restart", async ({ page }, testInfo) => {
  test.setTimeout(12 * 60_000);
  const stamp = `${Date.now()}-${testInfo.workerIndex}`;
  const backendPort = await reservePort();
  const backendData = path.join(dataRoot, "restart-backend-data");
  fs.mkdirSync(backendData, { recursive: true });
  let backend = await startBackend(backendPort, backendData);
  const backendBase = `http://127.0.0.1:${backendPort}`;

  await page.addInitScript((base) => window.localStorage.setItem("codetalk.apiBaseOverride", base), backendBase);
  try {
    const configName = `Restart Flash ${stamp}`;
    const workspaceName = `Restart SPDK ${stamp}`;
    const taskName = `Restart iSCSI login ${stamp}`;
    await configureFlashThroughUi(page, configName);
    await createWorkspaceThroughUi(page, workspaceName);
    const { runId, taskId } = await createAndRunTaskThroughUi(page, taskName, workspaceName);

    const execution = page.locator(".ct-v2-run-status").filter({ hasText: "执行状态" }).locator("strong");
    await expect.poll(async () => (await execution.textContent())?.trim(), {
      timeout: 7 * 60_000,
      intervals: [1_000, 2_000, 5_000],
    }).toBe("已阻断");
    await expect(page.locator(".ct-v2-run-deliverables").getByRole("button", { name: /report\.md/ })).toBeVisible();
    await page.screenshot({ path: path.join(dataRoot, `v3-restart-${stamp}-before.png`), fullPage: false });

    await stopBackend(backend);
    backend = await startBackend(backendPort, backendData);

    await page.goto(`/tasks/${encodeURIComponent(taskId)}/runs/${encodeURIComponent(runId)}`, { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: taskName })).toBeVisible({ timeout: 30_000 });
    await expect(page.locator(".ct-v2-run-status").filter({ hasText: "执行状态" }).locator("strong")).toHaveText("已阻断");
    await expect(page.locator(".ct-v2-run-deliverables").getByRole("button", { name: /report\.md/ })).toBeVisible();
    await page.screenshot({ path: path.join(dataRoot, `v3-restart-${stamp}-after.png`), fullPage: false });
  } finally {
    await stopBackend(backend);
  }
});

async function configureFlashThroughUi(page: import("@playwright/test").Page, name: string) {
  await page.goto("/settings", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /可选：内置模型与 RAG 检索/ }).click();
  await page.getByRole("button", { name: "新增" }).click();
  const form = page.locator("form").filter({ hasText: "新增 LLM 配置" });
  await form.getByPlaceholder("如：Claude / GPT-4o").fill(name);
  await form.getByPlaceholder("https://api.openai.com/v1").fill(process.env.CODETALK_E2E_LLM_BASE_URL ?? "https://api.deepseek.com/v1");
  await form.getByPlaceholder(/sk-|Ollama/).fill(apiKey);
  await form.getByPlaceholder(/gpt-4o|text-embedding/).fill("deepseek-v4-flash");
  await form.getByRole("button", { name: "测试连接" }).click();
  await expect(form).toContainText(/连接成功|测试成功|模型响应正常/, { timeout: 90_000 });
  await form.getByRole("button", { name: "保存配置" }).click();
  await expect(page.getByText(name, { exact: true })).toBeVisible();
}

async function createWorkspaceThroughUi(page: import("@playwright/test").Page, name: string) {
  await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
  await page.getByLabel("工作空间名称").fill(name);
  await page.getByLabel("代码仓库路径").fill(repoPath);
  await page.getByRole("button", { name: "创建工作空间" }).click();
  await page.waitForURL(/\/workspaces\/[0-9a-f-]{36}$/, { timeout: 30_000 });
}

async function createAndRunTaskThroughUi(page: import("@playwright/test").Page, taskName: string, workspaceName: string) {
  await page.goto("/tasks/new", { waitUntil: "domcontentloaded" });
  await page.getByRole("radio", { name: /基础源码 \+ 设计文档报告（内置模型）/ }).check();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByRole("textbox", { name: "任务名称 *" }).fill(taskName);
  await page.getByLabel("工作空间 *").selectOption({ label: workspaceName });
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByRole("textbox", { name: "分析目标 *" }).fill("验证后端重启后 iSCSI Login 源码分析、工件和质量状态可恢复。");
  const documentInput = page.locator('input[type="file"]');
  await documentInput.setInputFiles(designDocument);
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByRole("radio", { name: /速度型/ }).check();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByRole("button", { name: "保存并运行" }).click();
  await expect(page).toHaveURL(/\/tasks\/task_[^/]+\/runs\/task_run_/);
  const [, taskId, runId] = page.url().match(/\/tasks\/(task_[^/]+)\/runs\/(task_run_[^/]+)/) ?? [];
  expect(taskId).toMatch(/^task_/);
  expect(runId).toMatch(/^task_run_/);
  return { taskId, runId };
}

async function reservePort() {
  return await new Promise<number>((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close((error) => error ? reject(error) : resolve(typeof address === "object" && address ? address.port : 0));
    });
  });
}

async function startBackend(port: number, dataDir: string) {
  const backendDir = path.resolve(process.cwd(), "../backend");
  const python = [process.env.CODETALK_BACKEND_PYTHON, "python3.11", "python3"].find((candidate) => candidate && spawnSync(candidate, ["-c", "import uvicorn; import app.main"], { cwd: backendDir }).status === 0);
  if (!python) throw new Error("找不到可启动隔离后端的 Python 运行时");
  const child = spawn(python, ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(port)], {
    cwd: backendDir,
    env: {
      ...process.env,
      DATA_DIR: dataDir,
      SQLITE_DB: path.join(dataDir, "codetalk.db"),
      CORS_ORIGINS: `http://localhost:${frontendPort},http://127.0.0.1:${frontendPort}`,
      INTRANET_ALLOWED_HOSTS: '["api.deepseek.com"]',
    },
    stdio: "ignore",
  });
  await waitForHealthy(`http://127.0.0.1:${port}/health`);
  return child;
}

async function waitForHealthy(url: string) {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try { if ((await fetch(url)).ok) return; } catch { /* wait for process */ }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error(`隔离后端未能启动：${url}`);
}

async function stopBackend(child: ChildProcess) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.kill("SIGTERM");
  await new Promise<void>((resolve) => {
    const force = setTimeout(() => child.kill("SIGKILL"), 5_000);
    child.once("exit", () => { clearTimeout(force); resolve(); });
  });
}
