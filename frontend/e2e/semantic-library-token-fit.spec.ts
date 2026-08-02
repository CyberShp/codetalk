import { expect, test } from "@playwright/test";

const longCaseId = "CASE_20260802_9f0c2a7b3d4e5f678901234567890abcdef";
const longSourceRef = "import_20260802_abcdef1234567890abcdef1234567890";
const longTag = "tag_generated_abcdef1234567890abcdef";

const semanticCase = {
  semantic_id: "semantic_abcdef1234567890abcdef1234567890",
  case_id: longCaseId,
  feature: "feature_abcdef1234567890abcdef",
  module: "module_abcdef1234567890abcdef",
  test_level: "black_box",
  scenario: "登录失败后展示明确错误并允许重试",
  terms: [],
  tags: [longTag, "登录", "错误提示"],
  preconditions: ["用户已打开登录页"],
  actions: ["输入错误密码", "点击登录"],
  expected: ["页面提示密码错误"],
  assertion_style: "manual",
  interface: "api_abcdef1234567890abcdef",
  source_ref: longSourceRef,
  status: "active",
  created_at: "2026-08-02T10:00:00Z",
  updated_at: "2026-08-02T10:00:00Z",
  counts: { preconditions: 1, actions: 2, expected: 1 },
  matched_fields: [longTag],
  references: [],
  raw: {},
};

test("semantic library compacts generated ids in rows, tags, and detail header", async ({ page }) => {
  await page.route("**/api/workbench/semantic-cases/facets", async (route) => {
    await route.fulfill({
      json: {
        features: [{ value: semanticCase.feature, count: 1 }],
        modules: [{ value: semanticCase.module, count: 1 }],
        test_levels: [{ value: "black_box", count: 1 }],
        interfaces: [{ value: semanticCase.interface, count: 1 }],
        tags: [{ value: longTag, count: 1 }],
        statuses: [{ value: "active", count: 1 }],
        sources: [{ value: longSourceRef, count: 1 }],
      },
    });
  });
  await page.route(/\/api\/workbench\/semantic-cases(\?.*)?$/, async (route) => {
    await route.fulfill({
      json: { items: [semanticCase], total: 1, page: 1, page_size: 25, matched_fields: [] },
    });
  });
  await page.route(`**/api/workbench/semantic-cases/${semanticCase.semantic_id}`, async (route) => {
    await route.fulfill({ json: semanticCase });
  });

  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/semantic-library", { waitUntil: "domcontentloaded" });

  await expect(page.getByText(longCaseId, { exact: true })).toHaveCount(0);
  await expect(page.getByText(longSourceRef, { exact: true })).toHaveCount(0);
  await expect(page.getByText(longTag, { exact: true })).toHaveCount(0);
  await expect(page.getByText(/CASE_20260802_9…/)).toBeVisible();
  await expect(page.locator(".ct-asset-tags span").filter({ hasText: /tag_genera…/ })).toBeVisible();

  await page.getByRole("row").filter({ hasText: "登录失败后展示明确错误并允许重试" }).click();
  await expect(page.getByRole("complementary", { name: "语义用例详情" })).toBeVisible();
  await expect(page.getByText(/CASE_20260802_9…/)).toBeVisible();

  const layout = await page.evaluate(() => {
    const tag = document.querySelector(".ct-asset-tags span") as HTMLElement | null;
    const headerToken = document.querySelector(".ct-asset-detail > header span") as HTMLElement | null;
    return {
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
      tagWidth: tag?.getBoundingClientRect().width ?? 0,
      headerTokenWidth: headerToken?.getBoundingClientRect().width ?? 0,
    };
  });

  expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth + 1);
  expect(layout.tagWidth).toBeLessThan(130);
  expect(layout.headerTokenWidth).toBeLessThan(290);
});
