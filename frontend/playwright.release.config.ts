import { defineConfig, devices } from "@playwright/test";
import fs from "node:fs";
import { spawnSync } from "node:child_process";

const deployerPort = Number(process.env.CODETALK_DEPLOYER_PORT ?? "9000");
const deployerHost = process.env.CODETALK_DEPLOYER_HOST ?? "127.0.0.1";
const defaultDeployerPython =
  process.platform === "win32"
    ? ".venv\\Scripts\\python.exe"
    : ".venv/bin/python";

function commandExists(command: string): boolean {
  const result = spawnSync(command, ["--version"], { stdio: "ignore" });
  return result.status === 0;
}

function fallbackPython(): string {
  const candidates = process.platform === "win32"
    ? ["py -3.11", "py -3.10", "python"]
    : ["python3.12", "python3.11", "python3.10", "python3", "python"];
  return candidates.find((candidate) => {
    const [cmd, ...args] = candidate.split(" ");
    const result = spawnSync(cmd, [...args, "--version"], { stdio: "ignore" });
    return result.status === 0;
  }) ?? (process.platform === "win32" ? "python" : "python3");
}

const deployerPython =
  process.env.CODETALK_DEPLOYER_PYTHON ??
  (fs.existsSync(`../deployer/${defaultDeployerPython}`) && commandExists(`../deployer/${defaultDeployerPython}`)
    ? defaultDeployerPython
    : fallbackPython());

export default defineConfig({
  testDir: "./release-e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  timeout: 300_000,
  reporter: [["html", { open: "never" }]],
  use: {
    baseURL: `http://${deployerHost}:${deployerPort}`,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: `${deployerPython} start.py`,
    cwd: "../deployer",
    port: deployerPort,
    reuseExistingServer: true,
    timeout: 30_000,
    env: {
      CODETALK_DEPLOYER_HOST: deployerHost,
      CODETALK_DEPLOYER_PORT: String(deployerPort),
      CODETALK_DEPLOYER_NO_BROWSER: "1",
    },
  },
});
