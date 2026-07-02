import { expect, test } from "@playwright/test";
import { spawn, spawnSync, type ChildProcessWithoutNullStreams } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";

test.describe.configure({ mode: "serial" });

const repoRoot = path.resolve(__dirname, "..", "..");
const deployerDir = path.join(repoRoot, "deployer");
const deployerPort = Number(process.env.CODETALK_DEPLOYER_PORT ?? "9000");
const deployerUrl = `http://127.0.0.1:${deployerPort}`;
const coreFrontendPort = Number(process.env.CODETALK_DEPLOYER_FRONTEND_PORT ?? "3503");
const coreBackendPort = Number(process.env.CODETALK_DEPLOYER_BACKEND_PORT ?? "3504");
const runDir = fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-deployer-ui-"));
const configPath = path.join(runDir, "deployer-config.json");
const workspacePath = path.join(runDir, "workspace");
let deployerProcess: ChildProcessWithoutNullStreams | null = null;
const deployerOutput: string[] = [];

function deployerPython(): string {
  const configured = process.env.CODETALK_DEPLOYER_PYTHON;
  if (configured) return configured;
  const localPython =
    process.platform === "win32"
      ? path.join(deployerDir, ".venv", "Scripts", "python.exe")
      : path.join(deployerDir, ".venv", "bin", "python");
  const candidates = [
    localPython,
    "python3.12",
    "python3.11",
    "python3.10",
    "python3",
    "python",
  ].filter((candidate, index, all) => {
    if (all.indexOf(candidate) !== index) return false;
    return candidate !== localPython || fs.existsSync(localPython);
  });
  const usable = candidates.find((candidate) => {
    const result = spawnSync(
      candidate,
      [
        "-c",
        [
          "import sys",
          "assert sys.version_info >= (3, 10)",
          "import uvicorn",
          "import server",
        ].join("; "),
      ],
      { cwd: deployerDir, stdio: "ignore" },
    );
    return result.status === 0;
  });
  if (usable) return usable;
  throw new Error(
    "No Python >=3.10 interpreter with deployer dependencies found. " +
      "Set CODETALK_DEPLOYER_PYTHON or recreate deployer/.venv with Python 3.10+.",
  );
}

async function waitForDeployer() {
  const deadline = Date.now() + 30_000;
  let lastError = "";
  while (Date.now() < deadline) {
    if (deployerProcess?.exitCode !== null) {
      throw new Error(
        `deployer exited before becoming ready (code ${deployerProcess?.exitCode}):\n` +
          deployerOutput.slice(-20).join(""),
      );
    }
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

async function withOccupiedPort<T>(fn: (port: number) => Promise<T>): Promise<T> {
  const server = http.createServer((_, response) => {
    response.writeHead(200, { "Content-Type": "text/plain" });
    response.end("occupied");
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") {
    server.close();
    throw new Error("failed to allocate occupied test port");
  }
  try {
    return await fn(address.port);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
}

async function configureNativeDeployment(
  page: import("@playwright/test").Page,
  {
    workspace,
    frontendPort,
    backendPort,
  }: {
    workspace: string;
    frontendPort: number;
    backendPort: number;
  },
) {
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
  await page.getByLabel("工作目录路径").fill(workspace);

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
  await page.locator("#port-frontend").fill(String(frontendPort));
  await page.locator("#port-backend").fill(String(backendPort));
  await page.getByRole("button", { name: "前往下一步" }).click();
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
        CODETALK_DEPLOYER_FRONTEND_PORT: String(coreFrontendPort),
        CODETALK_DEPLOYER_BACKEND_PORT: String(coreBackendPort),
        CODETALK_DEPLOYER_INSTALL_GITNEXUS: "0",
        CODETALK_DEPLOYER_INSTALL_CGC: "0",
      },
      detached: process.platform !== "win32",
      stdio: "pipe",
    },
  );
  deployerProcess.stdout.on("data", (chunk) => deployerOutput.push(String(chunk)));
  deployerProcess.stderr.on("data", (chunk) => deployerOutput.push(String(chunk)));
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

test("deployment wizard shows an actionable port-conflict error without starting services", async ({ page }) => {
  await withOccupiedPort(async (occupiedPort) => {
    await configureNativeDeployment(page, {
      workspace: path.join(runDir, "conflict-workspace"),
      frontendPort: occupiedPort,
      backendPort: coreBackendPort,
    });

    await expect(page.getByRole("table", { name: "配置摘要" })).toContainText(String(occupiedPort));
    await expect(page.getByText(/DeepWiki|deepwiki|Joern|joern/)).toHaveCount(0);
    await page.locator("#btn-next").hover();
    await page.locator("#btn-next").click();

    const alert = page.getByRole("alert");
    await expect(alert).toContainText("端口冲突", { timeout: 30_000 });
    await expect(alert).toContainText(String(occupiedPort));
    await expect(page.getByRole("button", { name: /强制接管/ })).toBeVisible();

    const backendHealth = await page.request.get(`http://127.0.0.1:${coreBackendPort}/health`, {
      failOnStatusCode: false,
    }).catch(() => null);
    expect(backendHealth?.ok() ?? false).toBe(false);
  });
});

test("deployment wizard launches core services from real browser interactions", async ({ page }) => {
  const consoleMessages: string[] = [];
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) {
      consoleMessages.push(`${message.type()}: ${message.text()}`);
    }
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  await configureNativeDeployment(page, {
    workspace: workspacePath,
    frontendPort: coreFrontendPort,
    backendPort: coreBackendPort,
  });

  await expect(page.getByRole("table", { name: "配置摘要" })).toContainText(workspacePath);
  await expect(page.getByRole("table", { name: "配置摘要" })).toContainText("（无）");
  await expect(page.getByText(/DeepWiki|deepwiki|Joern|joern/)).toHaveCount(0);
  await expect(page.getByRole("table", { name: "配置摘要" })).toContainText(String(coreFrontendPort));
  await expect(page.getByRole("table", { name: "配置摘要" })).toContainText(String(coreBackendPort));

  await page.locator("#btn-next").hover();
  await page.locator("#btn-next").click();
  await expect(page.getByRole("log", { name: "部署日志" })).toContainText("所有核心服务已启动", {
    timeout: 180_000,
  });
  await expect(page.getByRole("heading", { name: "CodeTalk 已启动！" })).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByRole("link", { name: /前端界面/ })).toContainText(`localhost:${coreFrontendPort}`);
  await expect(page.getByRole("link", { name: /后端 API/ })).toContainText(`localhost:${coreBackendPort}`);
  await expect(page.locator('.service-url-card[data-service="gitnexus"]')).toBeHidden();
  await expect(page.locator('.service-url-card[data-service="cgc"]')).toBeHidden();

  const backendHealth = await page.request.get(`http://127.0.0.1:${coreBackendPort}/health`);
  expect(backendHealth.ok()).toBeTruthy();
  const frontendHome = await page.request.get(`http://127.0.0.1:${coreFrontendPort}/`);
  expect(frontendHome.ok()).toBeTruthy();
  expect(consoleMessages.filter((line) => !line.includes("Failed to load resource"))).toEqual([]);

  const savedConfig = JSON.parse(fs.readFileSync(configPath, "utf-8"));
  expect(savedConfig).toMatchObject({
    mode: "native",
    workspace_path: workspacePath,
    install_gitnexus: false,
    install_cgc: false,
    frontend_port: coreFrontendPort,
    backend_port: coreBackendPort,
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
