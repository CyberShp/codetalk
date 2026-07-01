import { test, expect } from "@playwright/test";
import fs from "node:fs";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

test.describe("Coverage analysis", () => {
  test.setTimeout(60_000);

  test("prevents duplicate coverage uploads from a real double click", async ({
    page,
    request,
  }) => {
    const analysisName = `double-upload-${Date.now()}`;

    await page.goto("/coverage", { waitUntil: "domcontentloaded" });

    await page.locator('input[type="text"]').first().fill(analysisName);
    await page.getByRole("button", { name: "选择文件" }).hover();
    await page.getByRole("button", { name: "选择文件" }).click();
    await page.locator('input[type="file"]').setInputFiles({
      name: "double-upload-function-hits.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(
        [
          "function_name,code_location,triggered,hit_count",
          "double_upload_gap,lib/nvmf/ctrlr.c:1-10,false,0",
        ].join("\n"),
        "utf8",
      ),
    });

    await expect(page.getByText("double-upload-function-hits.csv")).toBeVisible();

    const uploadRequests: string[] = [];
    page.on("request", (req) => {
      if (
        req.method() === "POST" &&
        new URL(req.url()).pathname === "/api/coverage/upload"
      ) {
        uploadRequests.push(req.url());
      }
    });
    const firstUpload = page.waitForRequest(
      (req) =>
        req.method() === "POST" &&
        new URL(req.url()).pathname === "/api/coverage/upload",
    );

    await page.getByRole("button", { name: "上传并解析" }).hover();
    await page.getByRole("button", { name: "上传并解析" }).dblclick();
    await firstUpload;

    await expect(page.getByRole("button", { name: "上传并解析" })).toBeDisabled();
    await expect(page.getByText(analysisName)).toBeVisible({ timeout: 15_000 });
    await expect.poll(() => uploadRequests.length).toBe(1);

    const listResp = await request.get(`${backendBase}/api/coverage/list`);
    expect(listResp.ok()).toBeTruthy();
    const analyses = (await listResp.json()) as Array<{ id: string; name: string }>;
    const created = analyses.filter((item) => item.name === analysisName);
    expect(created).toHaveLength(1);
    for (const item of created) {
      const deleteResp = await request.delete(`${backendBase}/api/coverage/${item.id}`);
      expect(deleteResp.ok()).toBeTruthy();
    }
  });

  test("prevents duplicate coverage AI analysis requests from a real double click", async ({
    page,
    request,
  }) => {
    const analysisName = `double-analysis-${Date.now()}`;

    await page.goto("/coverage", { waitUntil: "domcontentloaded" });

    await page.locator('input[type="text"]').first().fill(analysisName);
    await page.locator('input[type="file"]').setInputFiles({
      name: "double-analysis-function-hits.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(
        [
          "function_name,code_location,triggered,hit_count",
          "double_analysis_gap,lib/bdev/bdev.c:1-10,false,0",
        ].join("\n"),
        "utf8",
      ),
    });
    await page.getByRole("button", { name: "上传并解析" }).hover();
    await page.getByRole("button", { name: "上传并解析" }).click();
    await expect(page.getByText(analysisName)).toBeVisible({ timeout: 15_000 });

    const card = page
      .locator(".bg-surface-container-low")
      .filter({ hasText: analysisName })
      .first();
    await expect(card.getByRole("button", { name: "AI 分析" })).toBeVisible();

    const analyzeRequests: string[] = [];
    page.on("request", (req) => {
      if (
        req.method() === "POST" &&
        new URL(req.url()).pathname.endsWith("/analyze") &&
        new URL(req.url()).pathname.startsWith("/api/coverage/")
      ) {
        analyzeRequests.push(req.url());
      }
    });
    const firstAnalyze = page.waitForRequest(
      (req) =>
        req.method() === "POST" &&
        new URL(req.url()).pathname.endsWith("/analyze") &&
        new URL(req.url()).pathname.startsWith("/api/coverage/"),
    );

    await card.getByRole("button", { name: "AI 分析" }).hover();
    await card.getByRole("button", { name: "AI 分析" }).dblclick();
    await firstAnalyze;

    await expect(card.getByRole("button", { name: "AI 分析" })).toBeDisabled();
    await expect.poll(() => analyzeRequests.length).toBe(1);
    await expect(page.getByText("double_analysis_gap")).toBeVisible({ timeout: 20_000 });

    const listResp = await request.get(`${backendBase}/api/coverage/list`);
    expect(listResp.ok()).toBeTruthy();
    const analyses = (await listResp.json()) as Array<{ id: string; name: string }>;
    const created = analyses.filter((item) => item.name === analysisName);
    expect(created).toHaveLength(1);
    for (const item of created) {
      const deleteResp = await request.delete(`${backendBase}/api/coverage/${item.id}`);
      expect(deleteResp.ok()).toBeTruthy();
    }
  });

  test("prevents duplicate coverage reanalysis requests from a real double click", async ({
    page,
    request,
  }) => {
    const analysisName = `double-reanalysis-${Date.now()}`;

    await page.goto("/coverage", { waitUntil: "domcontentloaded" });

    await page.locator('input[type="text"]').first().fill(analysisName);
    await page.locator('input[type="file"]').setInputFiles({
      name: "double-reanalysis-function-hits.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(
        [
          "function_name,code_location,triggered,hit_count",
          "double_reanalysis_gap,lib/nvmf/subsystem.c:1-10,false,0",
        ].join("\n"),
        "utf8",
      ),
    });
    await page.getByRole("button", { name: "上传并解析" }).hover();
    await page.getByRole("button", { name: "上传并解析" }).click();
    await expect(page.getByText(analysisName)).toBeVisible({ timeout: 15_000 });

    const card = page
      .locator(".bg-surface-container-low")
      .filter({ hasText: analysisName })
      .first();
    await card.getByRole("button", { name: "AI 分析" }).hover();
    await card.getByRole("button", { name: "AI 分析" }).click();
    await expect(page.getByText("double_reanalysis_gap")).toBeVisible({ timeout: 20_000 });
    await expect(card.getByRole("button", { name: "重新分析" })).toBeVisible({
      timeout: 15_000,
    });

    const reanalysisRequests: string[] = [];
    page.on("request", (req) => {
      if (
        req.method() === "POST" &&
        new URL(req.url()).pathname.endsWith("/analyze") &&
        new URL(req.url()).pathname.startsWith("/api/coverage/")
      ) {
        reanalysisRequests.push(req.url());
      }
    });
    const firstReanalysis = page.waitForRequest(
      (req) =>
        req.method() === "POST" &&
        new URL(req.url()).pathname.endsWith("/analyze") &&
        new URL(req.url()).pathname.startsWith("/api/coverage/"),
    );

    await card.getByRole("button", { name: "重新分析" }).hover();
    await card.getByRole("button", { name: "重新分析" }).dblclick();
    await firstReanalysis;

    await expect(card.getByRole("button", { name: "重新分析" })).toBeDisabled();
    await expect(page.getByText("double_reanalysis_gap")).toBeVisible({ timeout: 20_000 });
    await expect.poll(() => reanalysisRequests.length).toBe(1);

    const listResp = await request.get(`${backendBase}/api/coverage/list`);
    expect(listResp.ok()).toBeTruthy();
    const analyses = (await listResp.json()) as Array<{ id: string; name: string }>;
    const created = analyses.filter((item) => item.name === analysisName);
    expect(created).toHaveLength(1);
    for (const item of created) {
      const deleteResp = await request.delete(`${backendBase}/api/coverage/${item.id}`);
      expect(deleteResp.ok()).toBeTruthy();
    }
  });

  test("prevents duplicate coverage delete requests after confirmation", async ({
    page,
    request,
  }) => {
    const analysisName = `double-delete-${Date.now()}`;

    await page.goto("/coverage", { waitUntil: "domcontentloaded" });

    await page.locator('input[type="text"]').first().fill(analysisName);
    await page.locator('input[type="file"]').setInputFiles({
      name: "double-delete-function-hits.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(
        [
          "function_name,code_location,triggered,hit_count",
          "double_delete_gap,lib/iscsi/iscsi.c:1-10,false,0",
        ].join("\n"),
        "utf8",
      ),
    });
    await page.getByRole("button", { name: "上传并解析" }).hover();
    await page.getByRole("button", { name: "上传并解析" }).click();
    await expect(page.getByText(analysisName)).toBeVisible({ timeout: 15_000 });

    const listResp = await request.get(`${backendBase}/api/coverage/list`);
    expect(listResp.ok()).toBeTruthy();
    const analyses = (await listResp.json()) as Array<{ id: string; name: string }>;
    const created = analyses.find((item) => item.name === analysisName);
    expect(created).toBeTruthy();

    const deleteRequests: string[] = [];
    page.on("request", (req) => {
      if (
        req.method() === "DELETE" &&
        new URL(req.url()).pathname === `/api/coverage/${created?.id}`
      ) {
        deleteRequests.push(req.url());
      }
    });

    page.once("dialog", async (dialog) => {
      expect(dialog.type()).toBe("confirm");
      expect(dialog.message()).toContain("确定删除这次覆盖率分析吗");
      await dialog.accept();
    });
    const card = page
      .locator(".bg-surface-container-low")
      .filter({ hasText: analysisName })
      .first();
    await card.getByRole("button", { name: "删除" }).hover();
    await card.getByRole("button", { name: "删除" }).dblclick();

    await expect(card).toHaveCount(0, { timeout: 15_000 });
    await expect.poll(() => deleteRequests.length).toBe(1);
  });

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

  test("keeps the entered analysis name after a failed upload retry", async ({
    page,
    request,
  }) => {
    const analysisName = `retry-name-${Date.now()}`;

    await page.goto("/coverage", { waitUntil: "domcontentloaded" });

    await page.locator('input[type="text"]').first().fill(analysisName);
    await page.locator('input[type="file"]').setInputFiles({
      name: "retry-broken-coverage.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("not_a_coverage_header\nno usable rows\n", "utf8"),
    });
    await page.getByRole("button", { name: "上传并解析" }).hover();
    await page.getByRole("button", { name: "上传并解析" }).click();

    await expect(page.locator('div[role="alert"]').filter({ hasText: "修复建议" })).toBeVisible();
    await expect(page.locator('input[type="text"]').first()).toHaveValue(analysisName);

    await page.locator('input[type="file"]').setInputFiles({
      name: "retry-valid-function-hits.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(
        [
          "function_name,code_location,triggered,hit_count",
          "retry_preserves_name,lib/nvmf/ctrlr.c:1-10,false,0",
        ].join("\n"),
        "utf8",
      ),
    });
    await page.getByRole("button", { name: "上传并解析" }).click();

    await expect(page.getByText(analysisName)).toBeVisible({ timeout: 15_000 });

    const listResp = await request.get(`${backendBase}/api/coverage/list`);
    expect(listResp.ok()).toBeTruthy();
    const analyses = (await listResp.json()) as Array<{ id: string; name: string }>;
    const created = analyses.filter((item) => item.name === analysisName);
    expect(created).toHaveLength(1);
    for (const item of created) {
      const deleteResp = await request.delete(`${backendBase}/api/coverage/${item.id}`);
      expect(deleteResp.ok()).toBeTruthy();
    }
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
      "recover_session,lib/nvmf/ctrlr.c:1-20,false,0",
      "parse_config,lib/bdev/bdev.c:1-30,true,3",
      "cleanup_temp,lib/bdev/bdev.c:22,0,0",
      "vfio_user_detach,lib/vfio-user/vfu.c:7-18,false,0",
      "rpc_config_apply,lib/rpc/rpc.c:4-12,false,0",
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

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出黑盒用例" }).hover();
    await page.getByRole("button", { name: "导出黑盒用例" }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe(`${analysisName}-black-box-cases.json`);
    const exportPath = test.info().outputPath("black-box-cases.json");
    await download.saveAs(exportPath);
    const exported = JSON.parse(fs.readFileSync(exportPath, "utf8")) as {
      version: string;
      name: string;
      dimensions: string[];
      cases: Array<{
        function_name: string | null;
        dimension: string;
        preconditions: string;
        inputs: string;
        steps: string[];
        expected: string;
        observable_signals: string[];
        suggested_spdk_test_dir: string;
      }>;
    };
    expect(exported).toMatchObject({
      version: "codetalk-coverage-black-box-export-v1",
      name: analysisName,
    });
    expect(exported.dimensions).toEqual([
      "normal_path",
      "invalid_input",
      "resource_shortage",
      "timeout",
      "reconnect",
      "concurrency",
      "recovery",
      "performance_degradation",
    ]);
    const recoverCases = exported.cases.filter((item) => item.function_name === "recover_session");
    expect(recoverCases).toHaveLength(exported.dimensions.length);
    expect(recoverCases.map((item) => item.dimension).sort()).toEqual(
      [...exported.dimensions].sort(),
    );
    for (const testCase of recoverCases) {
      expect(testCase.preconditions).toBeTruthy();
      expect(testCase.inputs).toMatch(
        /public|RPC|CLI|workload|invalid|timeout|concurrent|restart|latency|外部|公开|可见|公共|非法|超时|重试|并发|恢复|性能/i,
      );
      expect(testCase.steps.join("\n")).toContain("documented public configuration");
      expect(testCase.steps.join("\n")).toContain(
        "without requiring source modification or direct internal function invocation",
      );
      expect(testCase.steps.join("\n")).not.toMatch(/call recover_session|modify source/i);
      expect(testCase.expected).toBeTruthy();
      expect(testCase.observable_signals.length).toBeGreaterThan(0);
      expect(testCase.suggested_spdk_test_dir).toBeTruthy();
    }

    const sfmeaDownloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出 SFMEA" }).hover();
    await page.getByRole("button", { name: "导出 SFMEA" }).click();
    const sfmeaDownload = await sfmeaDownloadPromise;
    expect(sfmeaDownload.suggestedFilename()).toBe(`${analysisName}-sfmea.csv`);
    const sfmeaPath = test.info().outputPath("sfmea.csv");
    await sfmeaDownload.saveAs(sfmeaPath);
    const sfmeaText = fs.readFileSync(sfmeaPath, "utf8");
    for (const field of [
      "failure_mode",
      "cause",
      "effect",
      "detection",
      "severity",
      "occurrence",
      "detection_score",
      "rpn",
      "mitigation",
    ]) {
      expect(sfmeaText).toContain(field);
    }
    for (const dimension of exported.dimensions) {
      expect(sfmeaText).toContain(dimension);
    }
    expect(sfmeaText).toContain("lib/nvmf/ctrlr.c");
    expect(sfmeaText).toContain("lib/bdev/bdev.c");

    const fourPieceDownloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出四件套" }).hover();
    await page.getByRole("button", { name: "导出四件套" }).click();
    const fourPieceDownload = await fourPieceDownloadPromise;
    expect(fourPieceDownload.suggestedFilename()).toBe(`${analysisName}-four-piece.json`);
    const fourPiecePath = test.info().outputPath("four-piece.json");
    await fourPieceDownload.saveAs(fourPiecePath);
    const fourPiece = JSON.parse(fs.readFileSync(fourPiecePath, "utf8")) as {
      version: string;
      bundles: Array<{
        id: string;
        status: string;
        code_evidence: Array<{ file_path: string }>;
        flow_steps: Array<Record<string, unknown>>;
        sfmea: Array<Record<string, unknown>>;
        black_box_cases: Array<{ steps: string[]; diagnostics: { suggested_spdk_test_dir: string } }>;
      }>;
    };
    expect(fourPiece.version).toBe("codetalk-coverage-four-piece-v1");
    const e01 = fourPiece.bundles.find((bundle) => bundle.id === "E01");
    expect(e01?.status).toBe("generated");
    expect(e01?.code_evidence.some((item) => item.file_path === "lib/nvmf/ctrlr.c")).toBeTruthy();
    expect(e01?.flow_steps.length ?? 0).toBeGreaterThan(0);
    expect(e01?.sfmea.length ?? 0).toBe(exported.dimensions.length);
    expect(e01?.black_box_cases.length ?? 0).toBe(exported.dimensions.length);
    expect(
      e01?.black_box_cases.every((item) => item.diagnostics.suggested_spdk_test_dir === "test/nvmf"),
    ).toBeTruthy();
    const e05 = fourPiece.bundles.find((bundle) => bundle.id === "E05");
    expect(e05?.status).toBe("generated");
    expect(e05?.code_evidence.some((item) => item.file_path === "lib/bdev/bdev.c")).toBeTruthy();
    const e08 = fourPiece.bundles.find((bundle) => bundle.id === "E08");
    expect(e08?.status).toBe("generated");
    expect(e08?.code_evidence.some((item) => item.file_path === "lib/vfio-user/vfu.c")).toBeTruthy();
    expect(
      e08?.black_box_cases.every((item) => item.diagnostics.suggested_spdk_test_dir === "test/vfio_user"),
    ).toBeTruthy();
    const e10 = fourPiece.bundles.find((bundle) => bundle.id === "E10");
    expect(e10?.status).toBe("generated");
    expect(e10?.code_evidence.some((item) => item.file_path === "lib/rpc/rpc.c")).toBeTruthy();
    expect(
      e10?.black_box_cases.every((item) => item.diagnostics.suggested_spdk_test_dir === "test/rpc"),
    ).toBeTruthy();
    for (const testCase of [...(e01?.black_box_cases ?? []), ...(e05?.black_box_cases ?? [])]) {
      expect(testCase.steps.join("\n")).not.toMatch(/\b(call|invoke)\s+spdk_|直接调用内部函数|修改源码/i);
    }

    const rejudgeDownloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出复判报告" }).hover();
    await page.getByRole("button", { name: "导出复判报告" }).click();
    const rejudgeDownload = await rejudgeDownloadPromise;
    expect(rejudgeDownload.suggestedFilename()).toBe(`${analysisName}-rejudge.json`);
    const rejudgePath = test.info().outputPath("rejudge.json");
    await rejudgeDownload.saveAs(rejudgePath);
    const rejudge = JSON.parse(fs.readFileSync(rejudgePath, "utf8")) as {
      version: string;
      rubric: Record<string, number>;
      summary: { high_rpn_count: number; average_score: number; pass: boolean };
      high_rpn_rejudgements: Array<{
        evidence_real_path: boolean;
        hallucination_flags: string[];
        boundary_issues: string[];
        score: number;
      }>;
      gap_report: Record<string, unknown>;
    };
    expect(rejudge.version).toBe("codetalk-coverage-rejudge-v1");
    expect(rejudge.rubric).toMatchObject({
      evidence_truthfulness: 25,
      flow_completeness: 20,
      sfmea_quality: 20,
      black_box_quality: 20,
      hallucination_control: 10,
      usability: 5,
    });
    expect(rejudge.summary.high_rpn_count).toBeGreaterThan(0);
    expect(rejudge.summary.average_score).toBeGreaterThanOrEqual(80);
    expect(rejudge.summary.pass).toBe(true);
    expect(rejudge.high_rpn_rejudgements.every((item) => item.evidence_real_path)).toBeTruthy();
    expect(rejudge.high_rpn_rejudgements.every((item) => item.hallucination_flags.length === 0)).toBeTruthy();
    expect(rejudge.high_rpn_rejudgements.every((item) => item.boundary_issues.length === 0)).toBeTruthy();
    expect(rejudge.gap_report).toBeTruthy();

    page.once("dialog", async (dialog) => {
      expect(dialog.type()).toBe("confirm");
      expect(dialog.message()).toContain("确定删除这次覆盖率分析吗");
      await dialog.dismiss();
    });
    await card.getByRole("button", { name: "删除" }).hover();
    await card.getByRole("button", { name: "删除" }).click();
    await expect(page.getByText(analysisName)).toBeVisible();

    page.once("dialog", async (dialog) => {
      expect(dialog.type()).toBe("confirm");
      expect(dialog.message()).toContain("确定删除这次覆盖率分析吗");
      await dialog.accept();
    });
    await card.getByRole("button", { name: "删除" }).hover();
    await card.getByRole("button", { name: "删除" }).click();
    await expect(
      page.locator(".bg-surface-container-low").filter({ hasText: analysisName }),
    ).toHaveCount(0, { timeout: 15_000 });

    const afterDeleteResp = await request.get(`${backendBase}/api/coverage/list`);
    expect(afterDeleteResp.ok()).toBeTruthy();
    const afterDeleteAnalyses = (await afterDeleteResp.json()) as Array<{ name: string }>;
    expect(afterDeleteAnalyses.some((item) => item.name === analysisName)).toBe(false);
  });
});
