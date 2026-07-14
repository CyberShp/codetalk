import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

async function assertNoObviousOverlap(page: Page, selector: string) {
  const overlaps = await page.locator(selector).evaluateAll((nodes) => {
    const boxes = nodes
      .map((node) => {
        const el = node as HTMLElement;
        const rect = el.getBoundingClientRect();
        return {
          text: (el.innerText || el.getAttribute("aria-label") || el.title || el.tagName).trim(),
          left: rect.left,
          top: rect.top,
          right: rect.right,
          bottom: rect.bottom,
          width: rect.width,
          height: rect.height,
        };
      })
      .filter((box) => box.width > 8 && box.height > 8);
    const problems: string[] = [];
    for (let i = 0; i < boxes.length; i += 1) {
      for (let j = i + 1; j < boxes.length; j += 1) {
        const a = boxes[i];
        const b = boxes[j];
        const x = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
        const y = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
        if (x * y > 20) problems.push(`${a.text} overlaps ${b.text}`);
      }
    }
    return problems;
  });
  expect(overlaps).toEqual([]);
}

test("sidebar keeps current navigation baseline and collapses by click", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/", { waitUntil: "domcontentloaded" });

  const sidebar = page.locator("aside.ct-app-sidebar");
  const nav = page.getByRole("navigation", { name: "CodeTalk 主导航" });
  await expect(sidebar).toBeVisible();

  for (const label of ["工作台", "工作空间", "智能体编排", "AI 线程", "覆盖率分析", "设置"]) {
    await expect(nav.getByRole("link", { name: label })).toBeVisible();
  }
  for (const removedLabel of ["DeepWiki", "历史任务", "工具状态"]) {
    await expect(nav.getByRole("link", { name: removedLabel })).toHaveCount(0);
  }

  const collapseButton = page.getByRole("button", { name: "折叠 CodeTalk 导航" });
  await expect(collapseButton).toHaveAttribute("aria-expanded", "true");
  await collapseButton.hover();
  await collapseButton.click();

  await expect(page.locator("html")).toHaveAttribute("data-nav-collapsed", "true");
  await expect(page.getByRole("button", { name: "展开 CodeTalk 导航" })).toHaveAttribute(
    "aria-expanded",
    "false",
  );
  await expect(sidebar).toHaveClass(/is-collapsed/);

  const label = page.locator(".ct-app-sidebar__label", { hasText: "智能体编排" });
  await expect.poll(async () => label.evaluate((element) => {
    const styles = window.getComputedStyle(element);
    return {
      opacity: styles.opacity,
      maxWidth: styles.maxWidth,
      pointerEvents: styles.pointerEvents,
    };
  })).toEqual({
    opacity: "0",
    maxWidth: "0px",
    pointerEvents: "none",
  });
});

test("mobile app shell keeps navigation and primary controls usable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/", { waitUntil: "domcontentloaded" });

  await expect(page.getByText(/Unhandled Runtime Error|Build Error|Application error/i)).toHaveCount(0);
  await expect(page.getByText("AI 测试协同工作台")).toBeVisible();
  await expect(page.getByRole("link", { name: "打开 AI 线程" })).toBeVisible();
  await expect(page.getByRole("link", { name: "进入智能体编排" })).toBeVisible();

  const nav = page.getByRole("navigation", { name: "CodeTalk 主导航" });
  await expect(nav.getByRole("link", { name: "工作台" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "设置" })).toBeVisible();
  for (const removedLabel of ["DeepWiki", "历史任务", "工具状态"]) {
    await expect(nav.getByRole("link", { name: removedLabel })).toHaveCount(0);
  }

  await assertNoObviousOverlap(
    page,
    "aside a, aside button, main a, main button, .ct-home-topbar a, .ct-home-topbar button",
  );
});

test("removed legacy navigation pages stay deleted", async ({ page }) => {
  const removedRoutes = [
    { path: "/deepwiki", label: "DeepWiki" },
    { path: "/tools/status", label: "工具状态" },
    { path: "/tool-status", label: "工具状态" },
    { path: "/history", label: "历史任务" },
    { path: "/workbench/conversations", label: "AI 线程" },
    { path: "/workbench/conversations/legacy-thread", label: "AI 调查线程" },
  ];

  for (const route of removedRoutes) {
    const response = await page.goto(route.path, { waitUntil: "domcontentloaded" });
    expect(response?.status(), `${route.path} should not resurrect a removed page`).toBe(404);
    await expect(page.getByRole("main").getByText(route.label, { exact: true })).toHaveCount(0);
  }
});
