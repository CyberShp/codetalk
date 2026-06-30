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
