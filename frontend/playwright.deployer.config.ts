import { defineConfig, devices } from "@playwright/test";

const deployerPort = Number(process.env.CODETALK_DEPLOYER_PORT ?? "9000");

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 240_000,
  expect: {
    timeout: 10_000,
  },
  workers: 1,
  reporter: [["html", { open: "never" }]],
  use: {
    baseURL: `http://127.0.0.1:${deployerPort}`,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
