import { defineConfig, devices } from "@playwright/test";

const frontendPort = Number(process.env.CODETALK_FRONTEND_PORT ?? "3003");
const backendPort = Number(process.env.CODETALK_BACKEND_PORT ?? "3004");
const gitnexusPort = Number(process.env.GITNEXUS_PORT ?? process.env.CODETALK_GITNEXUS_PORT ?? "7100");
const browserHost = process.env.CODETALK_BROWSER_HOST ?? "localhost";
const reuseExistingServer = process.env.CODETALK_REUSE_EXISTING_SERVER !== "0";
const startGitNexus = process.env.CODETALK_PLAYWRIGHT_GITNEXUS === "1";
const chromiumExecutablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;

const webServer = [
  ...(startGitNexus
    ? [
        {
          command: "node scripts/start-playwright-gitnexus.mjs",
          port: gitnexusPort,
          reuseExistingServer,
          timeout: 30_000,
        },
      ]
    : []),
  {
    command: "node scripts/start-playwright-backend.mjs",
    url: `http://${browserHost}:${backendPort}/health`,
    reuseExistingServer,
    timeout: 30_000,
  },
  {
    command: "node scripts/start-playwright-frontend.mjs",
    url: `http://${browserHost}:${frontendPort}`,
    reuseExistingServer,
    timeout: 30_000,
  },
];

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
    launchOptions: chromiumExecutablePath
      ? { executablePath: chromiumExecutablePath }
      : undefined,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer,
});
