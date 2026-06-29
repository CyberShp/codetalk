import { defineConfig, devices } from "@playwright/test";

const frontendPort = Number(process.env.CODETALK_FRONTEND_PORT ?? "3005");
const backendPort = Number(process.env.CODETALK_BACKEND_PORT ?? "8100");
const browserHost = process.env.CODETALK_BROWSER_HOST ?? "localhost";
const reuseExistingServer = process.env.CODETALK_REUSE_EXISTING_SERVER !== "0";

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
      command: "node scripts/start-playwright-backend.mjs",
      port: backendPort,
      reuseExistingServer,
      timeout: 30_000,
    },
    {
      command: "node scripts/start-playwright-frontend.mjs",
      port: frontendPort,
      reuseExistingServer,
      timeout: 30_000,
    },
  ],
});
