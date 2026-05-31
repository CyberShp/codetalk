import { defineConfig, devices } from "@playwright/test";

const deployerPort = Number(process.env.CODETALK_DEPLOYER_PORT ?? "9000");
const deployerHost = process.env.CODETALK_DEPLOYER_HOST ?? "127.0.0.1";
const deployerPython =
  process.env.CODETALK_DEPLOYER_PYTHON ??
  (process.platform === "win32"
    ? ".venv\\Scripts\\python.exe"
    : ".venv/bin/python");

export default defineConfig({
  testDir: "./release-e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  timeout: 180_000,
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
    command: `${deployerPython} -m uvicorn server:app --host ${deployerHost} --port ${deployerPort}`,
    cwd: "../deployer",
    port: deployerPort,
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
