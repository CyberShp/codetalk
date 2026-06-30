import { test, expect } from "@playwright/test";

test("workspace report cards prefer backend report title over template id", async ({ page }) => {
  const now = "2026-05-31T00:00:00Z";

  await page.route("**/api/workspaces/ws-title/modules", async (route) => {
    await route.fulfill({ json: [] });
  });
  await page.route("**/api/workspaces/ws-title/versions", async (route) => {
    await route.fulfill({ json: [] });
  });
  await page.route(
    "**/api/workspaces/ws-title/materials/embedding-status",
    async (route) => {
      await route.fulfill({
        json: { total_materials: 0, embedded_materials: 0, total_chunks: 0, rag_ready: false },
      });
    },
  );
  await page.route("**/api/workspaces/ws-title", async (route) => {
    await route.fulfill({
      json: {
        id: "ws-title",
        name: "title-test",
        repo_path: "E:\\repo",
        indexed: 1,
        index_job: null,
        index_progress: 0,
        analyze_status: "done",
        analyze_progress: 100,
        last_index_error: null,
        created_at: now,
        updated_at: now,
        materials: [],
        reports: [
          {
            id: "r-project",
            workspace_id: "ws-title",
            task_id: "task-title",
            report_type: "project_structure",
            title: "项目结构初步理解",
            status: "completed",
            created_at: now,
          },
        ],
      },
    });
  });

  await page.goto("/workspaces/ws-title");

  await expect(page.getByRole("button", { name: "项目结构初步理解" })).toBeVisible();
  await expect(page.getByRole("button", { name: "project_structure" })).toHaveCount(0);
});
