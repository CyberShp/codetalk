import { test, expect } from "@playwright/test";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

test.describe("Coverage analysis", () => {
  test.setTimeout(60_000);

  test("shows actionable repair guidance when an uploaded coverage file is malformed", async ({
    page,
  }) => {
    const analysisName = `bad-coverage-${Date.now()}`;

    await page.goto("/coverage", { waitUntil: "domcontentloaded" });

    await page.locator('input[type="text"]').first().fill(analysisName);
    await page.getByRole("button", { name: "选择文件" }).hover();
    await page.getByRole("button", { name: "选择文件" }).click();
    await page.locator('input[type="file"]').setInputFiles({
      name: "broken-coverage.xml",
      mimeType: "text/xml",
      buffer: Buffer.from("<?xml version='1.0'?><coverage><unclosed_tag>", "utf8"),
    });

    await expect(page.getByText("broken-coverage.xml")).toBeVisible();
    await page.getByRole("button", { name: "上传并解析" }).hover();
    await page.getByRole("button", { name: "上传并解析" }).click();

    const alert = page.locator('div[role="alert"]').filter({ hasText: "修复建议" });
    await expect(alert).toContainText("请求参数有误，请检查输入");
    await expect(alert).toContainText("修复建议");
    await expect(alert).toContainText("Cobertura XML");
    await expect(alert).toContainText("JaCoCo XML");
    await expect(alert).toContainText("function_name + code_location + triggered/hit_count");
    await expect(alert).toContainText("特性名称、模块名称、代码路径、函数名称、是否覆盖、覆盖次数");
    await expect(page.getByText(analysisName)).toHaveCount(0);
  });

  test("uploads an internal function hit table and renders black-box recommendations", async ({
    page,
    request,
  }) => {
    const suffix = Date.now();
    const analysisName = `internal-hit-table-${suffix}`;

    await page.goto("/coverage", { waitUntil: "domcontentloaded" });

    await page.locator('input[type="text"]').first().fill(analysisName);

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
    await page.getByRole("button", { name: "上传并解析" }).hover();
    await page.getByRole("button", { name: "上传并解析" }).click();

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
    await expect(card).toContainText(/风险：高|风险：中/);

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
      workspace_id: null,
      source_format: "internal_function_hits",
    });
  });
});
