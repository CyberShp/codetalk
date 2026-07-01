import { expect, test } from "@playwright/test";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

test.describe.configure({ mode: "serial" });

const repoRoot = path.resolve(__dirname, "..", "..");
const deployerDir = path.join(repoRoot, "deployer");
const deployerPort = Number(process.env.CODETALK_DEPLOYER_PORT ?? "9000");
const deployerUrl = `http://127.0.0.1:${deployerPort}`;
const runDir = fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-deployer-ui-"));
const configPath = path.join(runDir, "deployer-config.json");
const workspacePath = path.join(runDir, "workspace");
let deployerProcess: ChildProcessWithoutNullStreams | null = null;

function deployerPython(): string {
  const configured = process.env.CODETALK_DEPLOYER_PYTHON;
  if (configured) return configured;
  const localPython =
    process.platform === "win32"
      ? path.join(deployerDir, ".venv", "Scripts", "python.exe")
      : path.join(deployerDir, ".venv", "bin", "python");
  return fs.existsSync(localPython) ? localPython : "python3";
}

async function waitForDeployer() {
  const deadline = Date.now() + 30_000;
  let lastError = "";
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${deployerUrl}/api/config`);
      if (response.ok) return;
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`deployer did not become ready: ${lastError}`);
}

async function stopDeployerServices() {
  try {
    await fetch(`${deployerUrl}/api/services/stop`, { method: "POST" });
  } catch {
    // The server may already be down during failure cleanup.
  }
}

test.beforeAll(async () => {
  deployerProcess = spawn(
    deployerPython(),
    ["-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", String(deployerPort)],
    {
      cwd: deployerDir,
      env: {
        ...process.env,
        CODETALK_DEPLOYER_CONFIG_PATH: configPath,
        CODETALK_DEPLOYER_NO_BROWSER: "1",
      },
      detached: process.platform !== "win32",
      stdio: "pipe",
    },
  );
  await waitForDeployer();
});

test.afterAll(async () => {
  await stopDeployerServices();
  if (deployerProcess?.pid) {
    if (process.platform === "win32") {
      spawn("taskkill", ["/T", "/F", "/PID", String(deployerProcess.pid)]);
    } else {
      try {
        process.kill(-deployerProcess.pid, "SIGTERM");
      } catch {
        // Process may already be gone.
      }
    }
  }
});

test("deployment wizard launches core services from real browser interactions", async ({ page }) => {
  const consoleMessages: string[] = [];
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) {
      consoleMessages.push(`${message.type()}: ${message.text()}`);
    }
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/deploy.html", { waitUntil: "domcontentloaded" });
  await expect(page).toHaveTitle(/CodeTalk 部署系统/);
  await expect(page.getByText(/DeepWiki|deepwiki|Joern|joern/)).toHaveCount(0);

  const nativeMode = page.getByRole("radio", { name: /本地原生部署/ });
  await nativeMode.hover();
  await nativeMode.click();
  await expect(nativeMode).toHaveAttribute("aria-checked", "true");

  await page.getByRole("button", { name: "前往下一步" }).click();
  await expect(page.getByRole("heading", { name: "环境检查" })).toBeVisible();
  await expect(page.getByRole("button", { name: "前往下一步" })).toBeEnabled({ timeout: 30_000 });

  await page.getByRole("button", { name: "前往下一步" }).click();
  await expect(page.getByLabel("工作目录路径")).toBeVisible();
  await page.getByLabel("工作目录路径").fill(workspacePath);

  const gitnexus = page.locator("#install-gitnexus");
  if (await gitnexus.isChecked()) {
    await gitnexus.hover();
    await gitnexus.click();
  }
  const cgc = page.locator("#install-cgc");
  if (await cgc.isChecked()) {
    await cgc.hover();
    await cgc.click();
  }

  await page.getByText("高级设置").click();
  await page.locator("#port-frontend").fill("3003");
  await page.locator("#port-backend").fill("3004");
  await page.getByRole("button", { name: "前往下一步" }).click();

  await expect(page.getByRole("table", { name: "配置摘要" })).toContainText(workspacePath);
  await expect(page.getByRole("table", { name: "配置摘要" })).toContainText("（无）");
  await expect(page.getByText(/DeepWiki|deepwiki|Joern|joern/)).toHaveCount(0);

  await page.locator("#btn-next").hover();
  await page.locator("#btn-next").click();
  await expect(page.getByRole("log", { name: "部署日志" })).toContainText("所有核心服务已启动", {
    timeout: 180_000,
  });
  await expect(page.getByRole("heading", { name: "CodeTalk 已启动！" })).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByRole("link", { name: /前端界面/ })).toContainText("localhost:3003");
  await expect(page.getByRole("link", { name: /后端 API/ })).toContainText("localhost:3004");
  await expect(page.locator('.service-url-card[data-service="gitnexus"]')).toBeHidden();
  await expect(page.locator('.service-url-card[data-service="cgc"]')).toBeHidden();

  const backendHealth = await page.request.get("http://127.0.0.1:3004/health");
  expect(backendHealth.ok()).toBeTruthy();
  const frontendHome = await page.request.get("http://127.0.0.1:3003/");
  expect(frontendHome.ok()).toBeTruthy();
  expect(consoleMessages.filter((line) => !line.includes("Failed to load resource"))).toEqual([]);

  const savedConfig = JSON.parse(fs.readFileSync(configPath, "utf-8"));
  expect(savedConfig).toMatchObject({
    mode: "native",
    workspace_path: workspacePath,
    install_gitnexus: false,
    install_cgc: false,
    frontend_port: 3003,
    backend_port: 3004,
  });
});

test("start page keeps removed services hidden and exposes core controls", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/start.html", { waitUntil: "domcontentloaded" });
  await expect(page).toHaveTitle(/CodeTalk 服务启动/);
  await expect(page.getByText(/DeepWiki|deepwiki|Joern|joern/)).toHaveCount(0);
  await expect(page.locator('.svc-card[data-svc="backend"]')).toBeVisible();
  await expect(page.locator('.svc-card[data-svc="frontend"]')).toBeVisible();
  await expect(page.locator('.svc-card[data-svc="gitnexus"]')).toBeHidden();
  await expect(page.locator('.svc-card[data-svc="cgc"]')).toBeHidden();

  const startAll = page.getByRole("button", { name: "一键启动全部服务" });
  await startAll.hover();
  await expect(startAll).toBeVisible();
});
