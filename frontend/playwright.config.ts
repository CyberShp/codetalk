import { defineConfig, devices } from "@playwright/test";

const frontendPort = Number(process.env.CODETALK_FRONTEND_PORT ?? "3003");
const backendPort = Number(process.env.CODETALK_BACKEND_PORT ?? "3004");
const frontendBindHost =
  process.env.CODETALK_FRONTEND_BIND_HOST ?? "0.0.0.0";
const backendBindHost = process.env.CODETALK_BACKEND_BIND_HOST ?? "0.0.0.0";
const browserHost = process.env.CODETALK_BROWSER_HOST ?? "localhost";
const reuseExistingServer = process.env.CODETALK_REUSE_EXISTING_SERVER !== "0";
const backendPython = process.env.CODETALK_BACKEND_PYTHON ?? "python3.11";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [["html", { open: "never" }]],
  use: {
    baseURL: `http://${browserHost}:${frontendPort}`,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: [
    {
      command:
        `cd ../backend && ${backendPython} -m uvicorn app.main:app --host ${backendBindHost} --port ${backendPort}`,
      port: backendPort,
      reuseExistingServer,
      timeout: 30_000,
    },
    {
      command: `NEXT_PUBLIC_API_URL=http://${browserHost}:${backendPort} npx next dev -H ${frontendBindHost} -p ${frontendPort}`,
      port: frontendPort,
      reuseExistingServer,
      timeout: 30_000,
    },
  ],
});
