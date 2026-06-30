import { expect, test } from "@playwright/test";

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
