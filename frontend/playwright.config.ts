import { defineConfig, devices } from "@playwright/test";

const frontendPort = Number(process.env.CODETALK_FRONTEND_PORT ?? "3005");
const backendPort = Number(process.env.CODETALK_BACKEND_PORT ?? "8100");
const frontendBindHost =
  process.env.CODETALK_FRONTEND_BIND_HOST ?? "0.0.0.0";
const backendBindHost = process.env.CODETALK_BACKEND_BIND_HOST ?? "0.0.0.0";
const browserHost = process.env.CODETALK_BROWSER_HOST ?? "localhost";

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
        `cd ../backend && python -m uvicorn app.main:app --host ${backendBindHost} --port ${backendPort}`,
      port: backendPort,
      reuseExistingServer: true,
      timeout: 30_000,
    },
    {
      command: `npx next dev -H ${frontendBindHost} -p ${frontendPort}`,
      port: frontendPort,
      reuseExistingServer: true,
      timeout: 30_000,
    },
  ],
});
