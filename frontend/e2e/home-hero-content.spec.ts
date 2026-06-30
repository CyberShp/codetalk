import { expect, test } from "@playwright/test";

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

test("home hero speaks to the broader AI testing workbench", async ({ page }) => {
  await mockEmptyWorkspaceList(page);

  await page.goto("/");

  await expect(page.getByText("AI 测试协同工作台")).toBeVisible();
  await expect(page.getByRole("heading", { name: /把代码理解\s*变成测试行动/ })).toBeVisible();
  await expect(page.getByText("需求、代码、工具执行器和测试证据")).toBeVisible();
  await expect(page.getByText("AI 测试中枢")).toBeVisible();
  await expect(page.getByText("CODETALK AI OS")).toBeVisible();
  await expect(page.getByText("Agent 编排")).toBeVisible();
  await expect(page.getByLabel("AI 测试中枢视觉面板").getByText("证据报告")).toBeVisible();
});

test("home current UI renders against the real backend without legacy navigation", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/", { waitUntil: "domcontentloaded" });

  const nav = page.getByRole("navigation", { name: "CodeTalk 主导航" });
  await expect(page.getByText(/Unhandled Runtime Error|Build Error|Application error/i)).toHaveCount(0);
  await expect(page.getByText("AI 测试协同工作台")).toBeVisible();
  await expect(page.getByRole("heading", { name: /把代码理解\s*变成测试行动/ })).toBeVisible();
  await expect(page.locator(".ct-home-topbar")).toBeVisible();
  await expect(page.locator(".ct-home-hero")).toBeVisible();
  await expect(page.getByLabel("AI 测试中枢视觉面板")).toBeVisible();

  for (const label of ["工作台", "工作空间", "智能体编排", "AI 线程", "覆盖率分析", "设置"]) {
    await expect(nav.getByRole("link", { name: label })).toBeVisible();
  }
  for (const removedLabel of ["DeepWiki", "历史任务", "工具状态"]) {
    await expect(nav.getByRole("link", { name: removedLabel })).toHaveCount(0);
  }

  const collapseButton = page.getByRole("button", { name: "折叠 CodeTalk 导航" });
  await collapseButton.hover();
  await collapseButton.click();
  await expect(page.locator("html")).toHaveAttribute("data-nav-collapsed", "true");
  await page.getByRole("button", { name: "展开 CodeTalk 导航" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-nav-collapsed", "false");

  const layout = await page.evaluate(() => {
    const body = getComputedStyle(document.body);
    const nodes = Array.from(
      document.querySelectorAll(
        ".ct-home-topbar h1, .ct-home-topbar p, .ct-home-topbar a, .ct-home-hero h2, .ct-home-primary-action, .ct-home-secondary-action",
      ),
    ) as HTMLElement[];
    const boxes = nodes.map((node, index) => {
      const rect = node.getBoundingClientRect();
      return {
        index,
        text: (node.innerText || node.getAttribute("aria-label") || node.tagName).trim(),
        left: rect.left,
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
        width: rect.width,
        height: rect.height,
      };
    }).filter((box) => box.width > 8 && box.height > 8);
    const overlaps: string[] = [];
    for (let i = 0; i < boxes.length; i += 1) {
      for (let j = i + 1; j < boxes.length; j += 1) {
        const a = boxes[i];
        const b = boxes[j];
        const x = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
        const y = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
        if (x * y > 20) overlaps.push(`${a.text || a.index} overlaps ${b.text || b.index}`);
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

  await page.getByRole("link", { name: "进入智能体编排" }).hover();
  await page.getByRole("link", { name: "进入智能体编排" }).click();
  await expect(page).toHaveURL(/\/workbench$/);
  await expect(page.getByRole("heading", { name: "智能体编排台" })).toBeVisible();
});

test("home desktop hero and topbar keep the optimized light layout", async ({ page }) => {
  await mockEmptyWorkspaceList(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/", { waitUntil: "domcontentloaded" });

  await expect(page.locator(".ct-home-topbar")).toBeVisible();
  await expect(page.locator(".ct-home-hero")).toBeVisible();
  await expect(page.getByRole("link", { name: "打开 AI 线程" })).toBeVisible();
  await expect(page.getByRole("link", { name: "进入智能体编排" })).toBeVisible();

  const layout = await page.evaluate(() => {
    const body = getComputedStyle(document.body);
    const hero = document.querySelector(".ct-home-hero") as HTMLElement;
    const topbarNodes = Array.from(
      document.querySelectorAll(".ct-home-topbar h1, .ct-home-topbar p, .ct-home-topbar button, .ct-home-topbar a"),
    ) as HTMLElement[];
    const heroNodes = Array.from(
      document.querySelectorAll(".ct-home-primary-action, .ct-home-secondary-action, .ct-home-metric"),
    ) as HTMLElement[];
    const boxes = [...topbarNodes, ...heroNodes].map((node, index) => {
      const rect = node.getBoundingClientRect();
      return {
        index,
        text: (node.innerText || node.getAttribute("aria-label") || node.tagName).trim(),
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
        const x = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
        const y = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
        if (x * y > 20) overlaps.push(`${a.text || a.index} overlaps ${b.text || b.index}`);
      }
    }
    const heroRect = hero.getBoundingClientRect();
    return {
      bodyBackground: body.backgroundColor,
      heroTop: heroRect.top,
      heroBottom: heroRect.bottom,
      heroWidth: heroRect.width,
      viewportHeight: window.innerHeight,
      overlaps,
    };
  });

  expect(luminance(layout.bodyBackground)).toBeGreaterThan(230);
  expect(layout.heroTop).toBeGreaterThanOrEqual(0);
  expect(layout.heroTop).toBeLessThan(160);
  expect(layout.heroBottom).toBeGreaterThan(520);
  expect(layout.heroBottom).toBeLessThanOrEqual(layout.viewportHeight + 4);
  expect(layout.heroWidth).toBeGreaterThan(900);
  expect(layout.overlaps).toEqual([]);
});

test("home reduced motion disables decorative atmosphere and pointer spotlight", async ({ page }) => {
  await mockEmptyWorkspaceList(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/", { waitUntil: "domcontentloaded" });

  await expect(page.locator(".ct-home-shell")).toBeVisible();
  await expect(page.locator(".ct-atmosphere")).toHaveCount(0);

  const before = await page.locator(".ct-home-shell").evaluate((node) => {
    const style = getComputedStyle(node as HTMLElement);
    return {
      x: style.getPropertyValue("--ct-home-x").trim(),
      y: style.getPropertyValue("--ct-home-y").trim(),
    };
  });

  await page.mouse.move(420, 260);
  await page.waitForTimeout(700);

  const after = await page.locator(".ct-home-shell").evaluate((node) => {
    const style = getComputedStyle(node as HTMLElement);
    return {
      x: style.getPropertyValue("--ct-home-x").trim(),
      y: style.getPropertyValue("--ct-home-y").trim(),
    };
  });

  expect(after).toEqual(before);
});

test("home mobile hero keeps primary paths tappable without horizontal overflow", async ({ page }) => {
  await mockEmptyWorkspaceList(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/", { waitUntil: "domcontentloaded" });

  await expect(page.getByRole("heading", { name: /把代码理解\s*变成测试行动/ })).toBeVisible();
  await expect(page.getByRole("link", { name: "打开 AI 线程" })).toBeVisible();
  await expect(page.getByRole("link", { name: "进入智能体编排" })).toBeVisible();

  const mobileLayout = await page.evaluate(() => {
    const viewportWidth = window.innerWidth;
    const nodes = Array.from(
      document.querySelectorAll(
        ".ct-home-topbar, .ct-home-hero, .ct-home-title, .ct-home-copy, .ct-home-primary-action, .ct-home-secondary-action, .ct-home-metric, .ct-home-product-stage, .ct-home-system-node",
      ),
    ) as HTMLElement[];
    const overflows = nodes
      .map((node) => {
        const rect = node.getBoundingClientRect();
        return {
          text: (node.innerText || node.getAttribute("aria-label") || node.className).toString().trim(),
          left: rect.left,
          right: rect.right,
          width: rect.width,
        };
      })
      .filter((box) => box.width > 4 && (box.left < -1 || box.right > viewportWidth + 1))
      .map((box) => `${box.text || "node"}:${Math.round(box.left)}-${Math.round(box.right)}`);
    const primary = document.querySelector(".ct-home-primary-action")!.getBoundingClientRect();
    const secondary = document.querySelector(".ct-home-secondary-action")!.getBoundingClientRect();
    const hero = document.querySelector(".ct-home-hero")!.getBoundingClientRect();
    return {
      overflows,
      primaryHeight: primary.height,
      secondaryHeight: secondary.height,
      heroWidth: hero.width,
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth,
    };
  });

  expect(mobileLayout.overflows).toEqual([]);
  expect(mobileLayout.documentWidth).toBeLessThanOrEqual(mobileLayout.viewportWidth + 1);
  expect(mobileLayout.heroWidth).toBeGreaterThan(320);
  expect(mobileLayout.primaryHeight).toBeGreaterThanOrEqual(40);
  expect(mobileLayout.secondaryHeight).toBeGreaterThanOrEqual(40);

  await page.getByRole("link", { name: "进入智能体编排" }).click();
  await expect(page).toHaveURL(/\/workbench$/);
  await expect(page.getByRole("heading", { name: "智能体编排台" })).toBeVisible();

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.getByRole("link", { name: "打开 AI 线程" }).click();
  await expect(page).toHaveURL(/\/ai$/);
  await expect(page.getByRole("heading", { name: "按项目管理持续对话" })).toBeVisible();
  await expect(page.getByText("这个项目还没有 AI 调查线程")).toBeVisible();
});
