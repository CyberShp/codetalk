import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

function luminance(rgb: string): number {
  const match = rgb.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  if (!match) return 0;
  const [, r, g, b] = match.map(Number);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

async function mockEmptyWorkspaceList(page: import("@playwright/test").Page) {
  await page.route("**/api/workspaces", async (route) => {
    await route.fulfill({ json: [] });
  });
}

async function mockHomeDashboardData(
  page: import("@playwright/test").Page,
  options: { delayAfterFirstLoad?: boolean } = {},
) {
  const taskRunId = "task_run_00c9143d749445c9ad887e2d9bb23bc8";
  const workflowId =
    "knowledge_enrichment_job_00c9143d749445c9ad887e2d9bb23bc8";
  let workspaceCalls = 0;
  let taskCalls = 0;
  let runCalls = 0;
  const maybeDelay = async (count: number) => {
    if (options.delayAfterFirstLoad && count > 1) {
      await new Promise((resolve) => setTimeout(resolve, 800));
    }
  };

  await page.route("**/api/workspaces", async (route) => {
    workspaceCalls += 1;
    await maybeDelay(workspaceCalls);
    await route.fulfill({
      json: [
        {
          id: "workspace-nvme",
          name: "nvme-cli",
          repo_path: "/Volumes/Media/nvme-cli",
          indexed: 1,
          last_index_error: "",
          reports: [],
        },
      ],
    });
  });
  await page.route("**/api/workbench/tasks?*", async (route) => {
    taskCalls += 1;
    await maybeDelay(taskCalls);
    await route.fulfill({
      json: { items: [], total: 0, page: 1, page_size: 8 },
    });
  });
  await page.route("**/api/workbench/tasks/history/runs", async (route) => {
    runCalls += 1;
    await maybeDelay(runCalls);
    await route.fulfill({
      json: {
        items: [
          {
            task_run_id: taskRunId,
            task_id: "task_run",
            attempt_number: 0,
            parent_task_run_id: "",
            workflow_id: workflowId,
            workspace_id: "workspace-nvme",
            execution_status: "prepared",
            quality_status: "not_started",
            delivery_status: "pending",
            started_at: "2026-08-02T06:00:00.000Z",
            completed_at: "",
            created_at: "2026-08-02T06:00:00.000Z",
            legacy: true,
          },
        ],
      },
    });
  });

  return { taskRunId, workflowId };
}

test("home presents a tester work queue instead of a decorative hero", async ({
  page,
}) => {
  await mockEmptyWorkspaceList(page);

  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "测试人员工作台" }),
  ).toBeVisible();
  await expect(
    page.getByText("先处理阻塞，再继续运行，最后沉淀证据。"),
  ).toHaveCount(0);
  await expect(page.getByText("首页聚焦今天能推进的测试工作")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "待处理队列" })).toBeVisible();
  await expect(page.getByRole("region", { name: "当前运行" })).toBeVisible();
  await expect(page.getByRole("region", { name: "项目基底" })).toBeVisible();
  await expect(page.getByTestId("home-primary-task")).toBeVisible();
  await expect(page.getByTestId("home-primary-project")).toBeVisible();
  await expect(page.getByText("AI 测试中枢")).toHaveCount(0);
  await expect(page.getByText("CODETALK AI OS")).toHaveCount(0);
  await expect(page.getByLabel("AI 测试中枢视觉面板")).toHaveCount(0);
  await expect(page.locator(".ct-ai-dock-wrap")).toHaveCount(0);
});

test("home compacts historical run ids and opens recoverable workbench links", async ({
  page,
}) => {
  const { taskRunId, workflowId } = await mockHomeDashboardData(page);

  await page.goto("/");

  const row = page
    .locator(".ct-home-queue-row", { hasText: "运行待处理" })
    .first();
  await expect(row).toBeVisible();
  await expect(row).toContainText("进入复盘");
  await expect(row).toContainText("…");
  await expect(row).not.toContainText(taskRunId);
  await expect(row).not.toContainText(workflowId);
  await expect(row).not.toContainText("运行 task_run");
  await expect(row).toHaveAttribute(
    "href",
    `/workbench?task_run_id=${encodeURIComponent(taskRunId)}`,
  );
});

test("home keeps cached data visible while returning from another route", async ({
  page,
}) => {
  const { workflowId } = await mockHomeDashboardData(page, {
    delayAfterFirstLoad: true,
  });

  await page.goto("/");
  await expect(
    page.locator(".ct-home-queue-row", { hasText: "运行待处理" }),
  ).toBeVisible();

  await page.goto("/settings", { waitUntil: "domcontentloaded" });
  await page.goto("/", { waitUntil: "domcontentloaded" });

  await expect(
    page.locator(".ct-home-queue-row", { hasText: "运行待处理" }),
  ).toBeVisible({
    timeout: 200,
  });
  await expect(page.locator(".ct-home-metric-value").first()).not.toHaveText(
    "--",
  );
  await expect(page.locator(".ct-home-panel .h-56 .animate-spin")).toHaveCount(
    0,
  );
  await expect(page.locator(".ct-home-queue-row").first()).not.toContainText(
    workflowId,
  );
});

test("home current UI renders against the real backend without legacy navigation", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/", { waitUntil: "domcontentloaded" });

  const nav = page.getByRole("navigation", { name: "CodeTalk 主导航" });
  await expect(
    page.getByText(/Unhandled Runtime Error|Build Error|Application error/i),
  ).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "测试人员工作台" }),
  ).toBeVisible();
  await expect(page.locator(".ct-home-workbench")).toBeVisible();
  await expect(page.getByRole("region", { name: "待处理队列" })).toBeVisible();
  await expect(page.getByRole("region", { name: "当前运行" })).toBeVisible();

  for (const label of ["工作台", "工作空间", "智能体编排", "AI 线程", "设置"]) {
    await expect(nav.getByRole("link", { name: label })).toBeVisible();
  }
  for (const removedLabel of ["DeepWiki", "历史任务", "工具状态"]) {
    await expect(nav.getByRole("link", { name: removedLabel })).toHaveCount(0);
  }

  const collapseButton = page.getByRole("button", {
    name: "折叠 CodeTalk 导航",
  });
  await collapseButton.hover();
  await collapseButton.click();
  await expect(page.locator("html")).toHaveAttribute(
    "data-nav-collapsed",
    "true",
  );
  await page.getByRole("button", { name: "展开 CodeTalk 导航" }).click();
  await expect(page.locator("html")).toHaveAttribute(
    "data-nav-collapsed",
    "false",
  );

  const layout = await page.evaluate(() => {
    const body = getComputedStyle(document.body);
    const nodes = Array.from(
      document.querySelectorAll(
        ".ct-home-workbench h1, .ct-home-workbench p, .ct-home-workbench a, .ct-home-workbench button, .ct-home-metric-value",
      ),
    ) as HTMLElement[];
    const boxes = nodes
      .map((node, index) => {
        const rect = node.getBoundingClientRect();
        return {
          index,
          text: (
            node.innerText ||
            node.getAttribute("aria-label") ||
            node.tagName
          ).trim(),
          left: rect.left,
          top: rect.top,
          right: rect.right,
          bottom: rect.bottom,
          width: rect.width,
          height: rect.height,
        };
      })
      .filter((box) => box.width > 8 && box.height > 8);
    const overlaps: string[] = [];
    for (let i = 0; i < boxes.length; i += 1) {
      for (let j = i + 1; j < boxes.length; j += 1) {
        const a = boxes[i];
        const b = boxes[j];
        const x = Math.max(
          0,
          Math.min(a.right, b.right) - Math.max(a.left, b.left),
        );
        const y = Math.max(
          0,
          Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top),
        );
        if (x * y > 20)
          overlaps.push(`${a.text || a.index} overlaps ${b.text || b.index}`);
      }
    }
    return {
      bodyBackground: body.backgroundColor,
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
      overlaps,
    };
  });

  expect(luminance(layout.bodyBackground)).toBeGreaterThan(230);
  expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth + 1);
  expect(layout.overlaps).toEqual([]);

  await page.getByRole("link", { name: "打开任务中心" }).hover();
  await page.getByRole("link", { name: "打开任务中心" }).click();
  await expect(page).toHaveURL(/\/tasks$/);
  await expect(page.getByRole("heading", { name: /任务/ })).toBeVisible();
});

test("home desktop workbench keeps the optimized light layout", async ({
  page,
}) => {
  await mockEmptyWorkspaceList(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/", { waitUntil: "domcontentloaded" });

  await expect(page.locator(".ct-home-workbench")).toBeVisible();
  await expect(page.getByTestId("home-primary-task")).toBeVisible();
  await expect(page.getByTestId("home-primary-project")).toBeVisible();
  await expect(page.getByRole("link", { name: "打开任务中心" })).toBeVisible();

  const layout = await page.evaluate(() => {
    const body = getComputedStyle(document.body);
    const workbench = document.querySelector(
      ".ct-home-workbench",
    ) as HTMLElement;
    const nodes = Array.from(
      document.querySelectorAll(
        ".ct-home-workbench h1, .ct-home-workbench p, .ct-home-workbench a, .ct-home-workbench button, .ct-home-metric-value",
      ),
    ) as HTMLElement[];
    const boxes = nodes.map((node, index) => {
      const rect = node.getBoundingClientRect();
      return {
        index,
        text: (
          node.innerText ||
          node.getAttribute("aria-label") ||
          node.tagName
        ).trim(),
        left: rect.left,
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
        width: rect.width,
        height: rect.height,
      };
    });
    const overlaps: string[] = [];
    for (let i = 0; i < boxes.length; i += 1) {
      for (let j = i + 1; j < boxes.length; j += 1) {
        const a = boxes[i];
        const b = boxes[j];
        const x = Math.max(
          0,
          Math.min(a.right, b.right) - Math.max(a.left, b.left),
        );
        const y = Math.max(
          0,
          Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top),
        );
        if (x * y > 20)
          overlaps.push(`${a.text || a.index} overlaps ${b.text || b.index}`);
      }
    }
    const workbenchRect = workbench.getBoundingClientRect();
    return {
      bodyBackground: body.backgroundColor,
      workbenchTop: workbenchRect.top,
      workbenchBottom: workbenchRect.bottom,
      workbenchWidth: workbenchRect.width,
      viewportHeight: window.innerHeight,
      overlaps,
    };
  });

  expect(luminance(layout.bodyBackground)).toBeGreaterThan(230);
  expect(layout.workbenchTop).toBeGreaterThanOrEqual(0);
  expect(layout.workbenchTop).toBeLessThan(90);
  expect(layout.workbenchBottom).toBeGreaterThan(620);
  expect(layout.workbenchBottom).toBeLessThanOrEqual(layout.viewportHeight + 4);
  expect(layout.workbenchWidth).toBeGreaterThan(900);
  expect(layout.overlaps).toEqual([]);
});

test("home reduced motion disables decorative atmosphere and pointer spotlight", async ({
  page,
}) => {
  await mockEmptyWorkspaceList(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/", { waitUntil: "domcontentloaded" });

  await expect(page.locator(".ct-home-shell")).toBeVisible();
  await expect(page.locator(".ct-atmosphere")).toHaveCount(0);

  await page.mouse.move(420, 260);
  await page.waitForTimeout(700);

  await expect(page.locator(".ct-home-workbench")).toBeVisible();
  await expect(page.locator(".ct-home-orbit-field")).toHaveCount(0);
  await expect(page.locator(".ct-home-satellite")).toHaveCount(0);
});

test("home shell avoids runtime animation dependencies and continuous decorative animations", async ({
  page,
}) => {
  await mockEmptyWorkspaceList(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/", { waitUntil: "domcontentloaded" });

  const packageJson = JSON.parse(
    fs.readFileSync(path.join(process.cwd(), "package.json"), "utf8"),
  ) as { dependencies?: Record<string, string> };
  const lockText = fs.readFileSync(
    path.join(process.cwd(), "package-lock.json"),
    "utf8",
  );
  expect(packageJson.dependencies ?? {}).not.toHaveProperty("gsap");
  expect(packageJson.dependencies ?? {}).not.toHaveProperty("@gsap/react");
  expect(lockText).not.toContain("node_modules/gsap");
  expect(lockText).not.toContain("node_modules/@gsap/react");

  const runningHomeAnimations = await page
    .locator(".ct-home-shell")
    .evaluate((shell) =>
      document
        .getAnimations()
        .filter((animation) => {
          const target =
            animation.effect instanceof KeyframeEffect
              ? animation.effect.target
              : null;
          return target instanceof Element && shell.contains(target);
        })
        .map((animation) => {
          const target =
            animation.effect instanceof KeyframeEffect
              ? animation.effect.target
              : null;
          const timing = animation.effect?.getComputedTiming();
          return {
            className: target instanceof HTMLElement ? target.className : "",
            playState: animation.playState,
            iterations: timing?.iterations,
            duration: timing?.duration,
          };
        })
        .filter(
          (animation) =>
            animation.playState !== "finished" &&
            animation.iterations === Infinity,
        ),
    );

  expect(
    runningHomeAnimations.filter(
      (animation) => !String(animation.className).includes("animate-pulse"),
    ),
  ).toEqual([]);
});

test("home mobile workbench keeps primary paths tappable without horizontal overflow", async ({
  page,
}) => {
  await mockEmptyWorkspaceList(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/", { waitUntil: "domcontentloaded" });

  await expect(
    page.getByRole("heading", { name: "测试人员工作台" }),
  ).toBeVisible();
  await expect(page.getByTestId("home-primary-task")).toBeVisible();
  await expect(page.getByTestId("home-primary-project")).toBeVisible();

  const mobileLayout = await page.evaluate(() => {
    const viewportWidth = window.innerWidth;
    const nodes = Array.from(
      document.querySelectorAll(
        ".ct-home-workbench, .ct-home-header, .ct-home-actions, .ct-home-metric",
      ),
    ) as HTMLElement[];
    const overflows = nodes
      .map((node) => {
        const rect = node.getBoundingClientRect();
        return {
          text: (
            node.innerText ||
            node.getAttribute("aria-label") ||
            node.className
          )
            .toString()
            .trim(),
          left: rect.left,
          right: rect.right,
          width: rect.width,
        };
      })
      .filter(
        (box) =>
          box.width > 4 && (box.left < -1 || box.right > viewportWidth + 1),
      )
      .map(
        (box) =>
          `${box.text || "node"}:${Math.round(box.left)}-${Math.round(box.right)}`,
      );
    const primary = document
      .querySelector('[data-testid="home-primary-task"]')!
      .getBoundingClientRect();
    const secondary = document
      .querySelector('[data-testid="home-primary-project"]')!
      .getBoundingClientRect();
    const workbench = document
      .querySelector(".ct-home-workbench")!
      .getBoundingClientRect();
    return {
      overflows,
      primaryHeight: primary.height,
      secondaryHeight: secondary.height,
      workbenchWidth: workbench.width,
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth,
    };
  });

  expect(mobileLayout.overflows).toEqual([]);
  expect(mobileLayout.documentWidth).toBeLessThanOrEqual(
    mobileLayout.viewportWidth + 1,
  );
  expect(mobileLayout.workbenchWidth).toBeGreaterThan(320);
  expect(mobileLayout.primaryHeight).toBeGreaterThanOrEqual(40);
  expect(mobileLayout.secondaryHeight).toBeGreaterThanOrEqual(40);

  await page.getByTestId("home-primary-task").click();
  await expect(page).toHaveURL(/\/tasks\/new$/);

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.getByTestId("home-primary-project").click();
  await expect(page).toHaveURL(/\/workspaces\/new$/);
});
