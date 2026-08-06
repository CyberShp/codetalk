import { expect, test } from "@playwright/test";

test("task center header avoids internal labels and explanatory copy", async ({ page }) => {
  await page.route("**/api/workbench/tasks**", async (route) => {
    await route.fulfill({
      json: {
        items: [
          {
            task_id: "task-style-check",
            name: "NVMe-oF 重连测试",
            description: "覆盖重连路径",
            tags: [],
            lifecycle_status: "ready",
            latest_run: null,
            skill_id: "skill.test.nvme",
            skill_name: "NVMe 测试 Skill",
            skill_version_id: "skillver_abcdef1234567890abcdef",
            skill_content_digest: "sha256:abcdef1234567890",
            workspace_id: "workspace_abcdef1234567890abcdef",
            workspace_name: "nvme-cli",
            quality_status: "not_checked",
            delivery_status: "none",
            execution_profile_id: "",
            input_values: {},
            execution_overrides: {},
            output_overrides: {},
            last_run_id: null,
            created_at: "2026-07-19T02:20:00Z",
            updated_at: "2026-07-19T02:25:00Z",
            archived_at: null,
          },
        ],
        total: 1,
        page: 1,
        page_size: 25,
      },
    });
  });
  await page.route("**/api/workspaces", async (route) => route.fulfill({ json: [] }));
  await page.route("**/api/skills/versions", async (route) => route.fulfill({ json: { items: [] } }));

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/tasks", { waitUntil: "domcontentloaded" });

  const header = page.locator(".ct-v2-task-center .ct-v2-page-header");
  await expect(header.getByRole("heading", { name: "任务中心" })).toBeVisible();
  await expect(header).not.toContainText("Workbench");
  await expect(header).not.toContainText("V2");
  await expect(header.locator("p")).toHaveCount(0);
  await expect(page.getByText("每次重试都会保留旧 Attempt")).toHaveCount(0);

  const spacing = await page.evaluate(() => {
    const buttons = Array.from(document.querySelectorAll(".ct-v2-page-actions > *")) as HTMLElement[];
    const [first, second] = buttons.map((item) => item.getBoundingClientRect());
    return {
      count: buttons.length,
      gap: first && second ? second.left - first.right : 0,
      headerHeight: document.querySelector(".ct-v2-page-header")?.getBoundingClientRect().height ?? 0,
    };
  });
  expect(spacing.count).toBe(2);
  expect(spacing.gap).toBeGreaterThanOrEqual(12);
  expect(spacing.headerHeight).toBeLessThanOrEqual(64);

  const workspaceCell = page.locator(".ct-v2-task-center tbody tr:first-child td:nth-child(5)");
  await expect(workspaceCell).toContainText("nvme-cli");
  const workspaceColumn = await workspaceCell.evaluate((cell) => {
    const text = cell.querySelector(".ct-v2-cell-text");
    return {
      cellDisplay: getComputedStyle(cell).display,
      textDisplay: text ? getComputedStyle(text).display : "",
    };
  });
  expect(workspaceColumn.cellDisplay).toBe("table-cell");
  expect(workspaceColumn.textDisplay).toBe("block");
});

test("knowledge center and artifact profiles share asset page shell", async ({ page }) => {
  await page.route("**/api/knowledge-center/incidents**", async (route) => route.fulfill({ json: [] }));
  await page.route("**/api/knowledge-center/patterns**", async (route) => route.fulfill({ json: [] }));
  await page.route("**/api/knowledge-center/import-jobs**", async (route) => route.fulfill({ json: [] }));
  await page.goto("/knowledge-center", { waitUntil: "domcontentloaded" });
  await expect(page.locator("main.ct-asset-page")).toBeVisible();
  await expect(page.locator(".ct-v2-page-header").getByRole("heading", { name: "经验知识库" })).toBeVisible();
  await expect(page.locator(".ct-v2-page-header")).not.toContainText("测试知识资产");
  await expect(page.locator(".ct-v2-page-header p")).toHaveCount(0);

  await page.route("**/api/workbench/artifact-profiles**", async (route) => {
    await route.fulfill({ json: [] });
  });
  await page.route("**/api/workspaces", async (route) => route.fulfill({ json: [] }));
  await page.goto("/artifact-profiles", { waitUntil: "domcontentloaded" });
  await expect(page.locator("main.ct-asset-page")).toBeVisible();
  await expect(page.locator(".ct-v2-page-header").getByRole("heading", { name: "交付件档案" })).toBeVisible();
  await expect(page.locator(".ct-v2-page-header")).not.toContainText("测试交付资产");
  await expect(page.locator(".ct-v2-page-header p")).toHaveCount(0);
  await expect(page.locator(".ct-artifact-profiles-workspace")).toBeVisible();
});
