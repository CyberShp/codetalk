import { expect, test } from "@playwright/test";

async function mockEmptyKnowledgeCenter(page: import("@playwright/test").Page) {
  await page.route("**/api/knowledge-center/incidents**", async (route) => {
    await route.fulfill({ json: [] });
  });
  await page.route("**/api/knowledge-center/patterns**", async (route) => {
    await route.fulfill({ json: [] });
  });
  await page.route("**/api/knowledge-center/import-jobs**", async (route) => {
    await route.fulfill({ json: [] });
  });
}

test("knowledge center keeps one title and guides empty review work", async ({ page }) => {
  await mockEmptyKnowledgeCenter(page);
  await page.setViewportSize({ width: 1280, height: 820 });

  await page.goto("/knowledge-center", { waitUntil: "domcontentloaded" });

  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { name: "经验知识库" })).toHaveCount(1);
  await expect(main.getByRole("heading", { name: "测试知识中心" })).toHaveCount(0);
  await expect(main.getByRole("navigation", { name: "知识中心标签页" })).toBeVisible();
  await expect(main.getByLabel("搜索知识")).toBeVisible();
  await expect(main.getByLabel("知识作用域")).toBeVisible();
  await expect(main.getByLabel("项目身份")).toBeVisible();

  await expect(main.getByText("暂无历史事件")).toHaveCount(0);
  await expect(main.getByRole("heading", { name: "还没有历史事件" })).toBeVisible();
  await expect(main.getByRole("button", { name: "导入事件" })).toBeVisible();
  await expect(main.getByRole("heading", { name: "选择事件查看详情" })).toBeVisible();

  const layout = await page.evaluate(() => {
    const grid = document.querySelector("[data-testid='knowledge-master-detail']") as HTMLElement | null;
    const list = document.querySelector("[data-testid='knowledge-list-pane']") as HTMLElement | null;
    const detail = document.querySelector("[data-testid='knowledge-detail-pane']") as HTMLElement | null;
    const main = document.querySelector("main") as HTMLElement;
    return {
      listWidth: list?.getBoundingClientRect().width ?? 0,
      detailWidth: detail?.getBoundingClientRect().width ?? 0,
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
      gridVisible: Boolean(grid),
      mainTop: main.getBoundingClientRect().top,
    };
  });

  expect(layout.gridVisible).toBe(true);
  expect(layout.detailWidth).toBeGreaterThan(layout.listWidth);
  expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth + 1);
  expect(layout.mainTop).toBeLessThan(20);
});
