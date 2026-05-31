import path from "node:path";
import { test, expect } from "@playwright/test";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "8100"}`;

test.describe("Coverage analysis", () => {
  test.setTimeout(60_000);

  test("uploads an internal function hit table and renders black-box recommendations", async ({
    page,
    request,
  }) => {
    const suffix = Date.now();
    const workspaceName = `coverage-e2e-${suffix}`;
    const analysisName = `internal-hit-table-${suffix}`;
    const repoPath = path.resolve(process.cwd(), "..");

    const wsResp = await request.post(`${backendBase}/api/workspaces`, {
      data: { name: workspaceName, repo_path: repoPath },
    });
    expect(wsResp.status()).toBe(201);
    const workspace = (await wsResp.json()) as { id: string };

    await page.goto("/coverage", { waitUntil: "domcontentloaded" });
    await expect(page.locator("select")).toContainText(workspaceName, {
      timeout: 15_000,
    });

    await page.locator('input[type="text"]').first().fill(analysisName);
    await page.locator("select").selectOption(workspace.id);

    const csv = [
      "function_name,code_location,triggered,hit_count",
      "recover_session,backend/app/main.py:1-20,false,0",
      "parse_config,backend/app/config.py:1-30,true,3",
      "cleanup_temp,backend/app/main.py:22,0,0",
    ].join("\n");

    await page.locator('input[type="file"]').setInputFiles({
      name: "internal-function-hits.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(csv, "utf8"),
    });

    await expect(page.getByText(analysisName)).toBeVisible({
      timeout: 15_000,
    });

    const card = page
      .locator(".bg-surface-container-low")
      .filter({ hasText: analysisName })
      .first();
    await expect(card).toContainText("internal_function_hits");
    await card.getByRole("button", { name: /AI/ }).click();

    await expect(page.getByText("recover_session")).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("cleanup_temp")).toBeVisible();
    await expect(page.getByText("parse_config")).toHaveCount(0);
    await expect(card).toContainText(/risk high|risk medium/);

    const listResp = await request.get(`${backendBase}/api/coverage/list`);
    expect(listResp.ok()).toBeTruthy();
    const analyses = (await listResp.json()) as Array<{
      name: string;
      status: string;
      workspace_id: string | null;
      source_format: string;
    }>;
    const created = analyses.find((item) => item.name === analysisName);
    expect(created).toMatchObject({
      status: "analyzed",
      workspace_id: workspace.id,
      source_format: "internal_function_hits",
    });
  });
});
