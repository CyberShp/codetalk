import { expect, test } from "@playwright/test";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";

const frontendPort = Number(process.env.CODETALK_FRONTEND_PORT ?? "3205");
const backendPort = Number(process.env.CODETALK_BACKEND_PORT ?? "3004");

const repoRoot = path.resolve(__dirname, "..", "..");
const deployerDir = path.join(repoRoot, "deployer");
const deployConfig = path.join(deployerDir, ".deploy-config.json");
const backendEnv = path.join(repoRoot, "backend", ".env");
const frontendEnv = path.join(repoRoot, "frontend", ".env.local");

const snapshots = new Map<string, string | null>();

function rememberFile(filePath: string) {
  snapshots.set(
    filePath,
    fs.existsSync(filePath) ? fs.readFileSync(filePath, "utf8") : null,
  );
}

function restoreFile(filePath: string) {
  const original = snapshots.get(filePath);
  if (original === undefined) return;
  if (original === null) {
    fs.rmSync(filePath, { force: true });
    return;
  }
  fs.writeFileSync(filePath, original, "utf8");
}

async function canBind(port: number, host = "127.0.0.1"): Promise<boolean> {
  return await new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.listen(port, host, () => {
      server.close(() => resolve(true));
    });
  });
}

async function canBindEverywhere(port: number): Promise<boolean> {
  return (await canBind(port, "127.0.0.1")) && (await canBind(port, "0.0.0.0"));
}

async function waitForPortReleased(port: number) {
  await expect.poll(() => canBindEverywhere(port), { timeout: 30_000 }).toBeTruthy();
}

function expectProductApiOk(response: import("@playwright/test").Response) {
  expect(response.status(), `${response.request().method()} ${response.url()}`).toBeLessThan(500);
}

async function expectNoContinuousDecorativeAnimations(
  page: import("@playwright/test").Page,
  selector: string,
) {
  const runningAnimations = await page.locator(selector).evaluate((root) =>
    document
      .getAnimations()
      .filter((animation) => {
        const target = animation.effect instanceof KeyframeEffect ? animation.effect.target : null;
        return target instanceof Element && root.contains(target);
      })
      .map((animation) => {
        const target = animation.effect instanceof KeyframeEffect ? animation.effect.target : null;
        const timing = animation.effect?.getComputedTiming();
        return {
          className: target instanceof HTMLElement ? target.className : "",
          tagName: target instanceof Element ? target.tagName : "",
          playState: animation.playState,
          iterations: timing?.iterations,
          duration: timing?.duration,
        };
      })
      .filter((animation) => animation.playState !== "finished" && animation.iterations === Infinity),
  );

  const allowedFeedbackAnimations = runningAnimations.filter((animation) => {
    const className = String(animation.className);
    return className.includes("spinner") || className.includes("loading") || className.includes("spinning");
  });
  expect(runningAnimations).toEqual(allowedFeedbackAnimations);
}

async function uncheckIfChecked(selector: string, page: import("@playwright/test").Page) {
  const checkbox = page.locator(selector);
  if (await checkbox.isChecked()) {
    await checkbox.uncheck({ force: true });
  }
}

test.describe.serial("internal release click-through", () => {
  test.beforeAll(() => {
    [deployConfig, backendEnv, frontendEnv].forEach(rememberFile);

    fs.writeFileSync(
      deployConfig,
      JSON.stringify(
        {
          mode: "native",
          workspace_path: "./workspace",
          install_gitnexus: false,
          install_cgc: false,
          gitnexus_port: 8210,
          cgc_port: 8200,
          repos_path: "./workspace/repos",
          frontend_port: frontendPort,
          backend_port: backendPort,
          cors_origins: `http://localhost:${frontendPort},http://127.0.0.1:${frontendPort}`,
          openai_api_key: "",
        },
        null,
        2,
      ),
      "utf8",
    );
  });

  test.afterAll(() => {
    [frontendEnv, backendEnv, deployConfig].forEach(restoreFile);
  });

  test("deployer pages avoid continuous decorative animations", async ({ page }) => {
    for (const pathName of ["/", "/deploy.html", "/start.html"]) {
      await page.goto(pathName, { waitUntil: "domcontentloaded" });
      await expectNoContinuousDecorativeAnimations(page, "body");
    }
  });

  test("deploy, start, and validate core product by clicking through the UI", async ({
    page,
    context,
  }) => {
    let servicesStopped = false;
    let startAttempted = false;

    try {
    await expect.poll(() => canBindEverywhere(backendPort), { timeout: 5_000 }).toBeTruthy();
    await expect.poll(() => canBindEverywhere(frontendPort), { timeout: 5_000 }).toBeTruthy();

    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(page.locator('a[href="/deploy.html"]')).toBeVisible();
    await expect(page.locator('a[href="/start.html"]')).toBeVisible();

    await page.locator('a[href="/deploy.html"]').click();
    await expect(page.locator("#btn-next")).toBeVisible();

    await page.locator('.mode-card[data-mode="native"]').click();
    await expect(page.locator("#btn-next")).toBeEnabled();
    await page.locator("#btn-next").click();

    await expect(page.locator("#checks-list .check-item")).not.toHaveCount(0, {
      timeout: 30_000,
    });
    await expect(page.locator("#btn-next")).toBeEnabled({ timeout: 30_000 });
    await page.locator("#btn-next").click();

    await expect(page.locator("#install-deepwiki")).toHaveCount(0);
    await expect(page.getByText("DeepWiki")).toHaveCount(0);
    await uncheckIfChecked("#install-gitnexus", page);
    await uncheckIfChecked("#install-cgc", page);

    const advanced = page.locator("#advanced-section");
    if (!(await advanced.evaluate((el) => (el as HTMLDetailsElement).open))) {
      await page.locator("#advanced-section summary").click();
    }
    await page.locator("#port-frontend").fill(String(frontendPort));
    await page.locator("#port-backend").fill(String(backendPort));
    await page
      .locator("#cors-origins")
      .fill(`http://localhost:${frontendPort},http://127.0.0.1:${frontendPort}`);
    await page.locator("#btn-next").click();

    await expect(page.locator("#review-content")).toContainText(String(frontendPort));
    await expect(page.locator("#review-content")).toContainText(String(backendPort));

    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.locator('a[href="/start.html"]').click();
    await expect(page.locator("#cfg-frontend-url")).toContainText(String(frontendPort));
    await expect(page.locator("#cfg-backend-url")).toContainText(String(backendPort));
    await expect(page.locator("#port-frontend")).toHaveText(`:${frontendPort}`);

    startAttempted = true;
    await page.locator("#btn-start-all").click();
    try {
      await expect(page.locator("#success-banner.visible")).toBeVisible({
        timeout: 120_000,
      });
    } catch (error) {
      const logText = await page.locator("#terminal-log").innerText();
      if (!logText.includes(String(frontendPort)) || !logText.includes("PID")) {
        throw error;
      }
      await expect(page.locator("#terminal-log")).toContainText(String(frontendPort));
      await page.locator("#btn-start-all").click();
      await expect(page.locator("#success-banner.visible")).toBeVisible({
        timeout: 120_000,
      });
    }
    await expect(page.locator("#dot-backend")).toHaveClass(/dot-running/);
    await expect(page.locator("#dot-frontend")).toHaveClass(/dot-running/);

    const [appPage] = await Promise.all([
      context.waitForEvent("page"),
      page.locator("#open-ct-link").click(),
    ]);
    await appPage.waitForLoadState("domcontentloaded");
    await expect(appPage).toHaveTitle(/CodeTalk/i);
    await expect(appPage.locator("body")).toContainText("CODETALK");

    const workspaceListResponse = appPage.waitForResponse((response) => {
      return response.url().includes("/api/workspaces")
        && response.request().method() === "GET";
    });
    await appPage.locator('a[href="/workspaces"]').click();
    expectProductApiOk(await workspaceListResponse);
    await expect(appPage).toHaveURL(/\/workspaces$/);
    await expect(appPage.locator('a[href="/workspaces/new"]')).toBeVisible();

    await appPage.locator('a[href="/workspaces/new"]').first().click();
    await expect(appPage).toHaveURL(/\/workspaces\/new$/);
    await expect(appPage.locator('input[type="text"]').first()).toBeVisible();

    await appPage
      .locator('input[type="text"]')
      .first()
      .fill(`release-click-${Date.now()}`);
    await appPage.locator('input[type="text"]').nth(1).fill(repoRoot);
    const workspaceCreateResponse = appPage.waitForResponse((response) => {
      return response.url().includes("/api/workspaces")
        && response.request().method() === "POST";
    });
    await appPage.locator('button[type="submit"]').click();
    expectProductApiOk(await workspaceCreateResponse);
    await expect(appPage).toHaveURL(/\/workspaces\/[^/]+$/, { timeout: 30_000 });

    const llmSettingsResponse = appPage.waitForResponse((response) => {
      return response.url().includes("/api/settings/llm")
        && response.request().method() === "GET";
    });
    await appPage.locator('a[href="/settings"]').click();
    expectProductApiOk(await llmSettingsResponse);
    await expect(appPage).toHaveURL(/\/settings$/);
    await expect(appPage.locator("h1, h2").first()).toBeVisible();

    await appPage.close();

    await page.bringToFront();
    await page.locator("#btn-stop-all").click();
    await expect(page.locator("#dot-backend")).not.toHaveClass(/dot-running/, {
      timeout: 30_000,
    });
    await expect(page.locator("#dot-frontend")).not.toHaveClass(/dot-running/, {
      timeout: 30_000,
    });

    await waitForPortReleased(backendPort);
    await waitForPortReleased(frontendPort);
    servicesStopped = true;
    } finally {
      if (startAttempted && !servicesStopped) {
        try {
          await page.bringToFront();
          await page.goto("/start.html", { waitUntil: "domcontentloaded" });
          await page.locator("#btn-stop-all").click();
          await waitForPortReleased(backendPort);
          await waitForPortReleased(frontendPort);
        } catch {
          // The test failure that triggered cleanup should remain the reported error.
        }
      }
    }
  });
});
