import { expect, test } from "@playwright/test";

const runId = "task_run_quality_fixture";
const taskId = "task_quality_fixture";

type FixtureState = "ready" | "limited" | "repairing" | "repaired" | "blocked" | "unavailable";

test.describe("F012 quality evaluation cockpit", () => {
  for (const state of ["ready", "limited", "repairing", "repaired", "blocked", "unavailable"] as const) {
    test(`renders ${state} quality evaluation without exposing hidden truth`, async ({ page }) => {
      await installCockpitFixture(page, state);
      const evaluationResponse = page.waitForResponse((response) => response.url().includes("/quality-evaluation"));
      await page.goto(`/tasks/${taskId}/runs/${runId}`, { waitUntil: "domcontentloaded" });
      const responseBody = await (await evaluationResponse).text();

      const panel = page.getByLabel("独立质量评估");
      await expect(panel.getByRole("heading", { name: "独立质量评估" })).toBeVisible();
      expect(responseBody).not.toContain("hidden-");
      expect(responseBody).not.toContain("truth://");
      await expect(page.getByText(/hidden-/)).toHaveCount(0);
      await expect(page.getByText(/truth:\/\//)).toHaveCount(0);
      await expect(page.getByRole("button", { name: /修复质量问题并重试/ })).toHaveCount(0);

      if (state === "unavailable") {
        await expect(panel.getByText("独立质量评估当前不可用")).toBeVisible();
        await expect(page.getByText("查看现有运行质量检查")).toBeVisible();
        return;
      }
      await expect(page.getByRole("heading", { name: "质量结果" })).toHaveCount(0);
      await expect(panel.getByRole("button", { name: "展开 Accuracy 详情" })).toBeVisible();
      await expect(panel.getByRole("button", { name: "展开 Breadth 详情" })).toBeVisible();
      await expect(panel.getByRole("button", { name: "展开 Depth 详情" })).toBeVisible();
      if (state === "repairing") {
        await expect(panel.getByLabel("自动修复进度")).toBeVisible();
        await expect(panel.getByText("第 1 / 2 次")).toBeVisible();
        await expect(page.getByRole("button", { name: /重试/ })).toHaveCount(0);
        return;
      }
      if (state === "blocked") {
        await expect(panel.getByText("终态阻断", { exact: true })).toBeVisible();
        await expect(panel.getByText("修复预算已耗尽")).toBeVisible();
        await expect(panel.getByText("查看自动修复前后对比")).toBeVisible();
        const runHeader = page.locator(".ct-v2-run-header");
        await expect(runHeader.getByText("可交付", { exact: true })).toHaveCount(0);
        await expect(runHeader.getByText("已阻断", { exact: true })).toHaveCount(2);
        await expect(page.getByRole("button", { name: "重新运行质量修复" })).toHaveCount(1);
        await panel.getByRole("button", { name: "展开 Accuracy 详情" }).click();
        await expect(panel.getByText("生成事实 CLAIM-reset-42")).toBeVisible();
        await expect(panel.getByText("公开证据未能闭合该事实陈述")).toBeVisible();
        await expect(panel.getByText("下一步：核对公开源码证据与事实陈述，并修正不一致内容")).toBeVisible();
        await panel.getByRole("button", { name: "展开 Breadth 详情" }).click();
        await expect(panel.getByText("协议覆盖项 REF-BREADTH01")).toBeVisible();
        await panel.getByRole("button", { name: "展开 Depth 详情" }).click();
        await expect(panel.getByText("因果链节点 REF-DEPTH0001")).toBeVisible();
        return;
      }
      if (state === "limited") {
        await expect(panel.getByText("受限", { exact: true }).first()).toBeVisible();
        await panel.locator(".ct-v2-quality-evaluation-limitations summary").click();
        await expect(panel.getByText("L3_NOT_RUN")).toBeVisible();
      } else if (state === "ready") {
        await expect(panel.getByText("可交付", { exact: true })).toBeVisible();
      }

      await panel.getByRole("button", { name: "展开 Accuracy 详情" }).click();
      await expect(panel.getByText("claim_precision")).toBeVisible();
      if (state === "repaired") {
        await expect(panel.getByText("未通过 · 0/1", { exact: true })).toBeVisible();
        await expect(panel.getByText("通过 · 1/1", { exact: true })).toBeVisible();
        await expect(panel.getByText("查看自动修复前后对比")).toBeVisible();
      }
    });
  }

  test("polls a pending evaluation until the report becomes available", async ({ page }) => {
    const fixture = await installCockpitFixture(page, "ready", "independent_benchmark", { pendingResponses: 2 });
    await page.goto(`/tasks/${taskId}/runs/${runId}`, { waitUntil: "domcontentloaded" });

    const panel = page.getByLabel("独立质量评估");
    await expect(panel.getByRole("button", { name: "展开 Accuracy 详情" })).toBeVisible({ timeout: 10_000 });
    expect(fixture.qualityRequestCount()).toBeGreaterThanOrEqual(3);
  });

  test("operational audit omits benchmark-only gold recall", async ({ page }) => {
    await installCockpitFixture(page, "ready", "operational");
    await page.goto(`/tasks/${taskId}/runs/${runId}`, { waitUntil: "domcontentloaded" });
    const panel = page.getByLabel("独立质量评估");
    await expect(panel.getByText("运行内质量审计", { exact: true })).toBeVisible();
    await panel.getByRole("button", { name: "展开 Accuracy 详情" }).click();
    await expect(panel.getByText("claim_precision")).toBeVisible();
    await expect(panel.getByText("gold_recall")).toHaveCount(0);
  });

  for (const capture of [
    { state: "ready", width: 1440, height: 900 },
    { state: "blocked", width: 1280, height: 800 },
    { state: "repairing", width: 390, height: 844 },
  ] as const) {
    test(`captures ${capture.width}x${capture.height} ${capture.state} audit evidence`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width: capture.width, height: capture.height });
      await installCockpitFixture(page, capture.state);
      await page.goto(`/tasks/${taskId}/runs/${runId}`, { waitUntil: "domcontentloaded" });
      const panel = page.getByLabel("独立质量评估");
      await expect(panel).toBeVisible();
      if (capture.state === "blocked") {
        const diagnostics = page.getByRole("button", { name: "技术诊断" });
        await expect(page.locator(".ct-v2-run-actions").getByRole("button", { name: "技术诊断" })).toBeVisible();
        expect(rectanglesOverlap(await diagnostics.boundingBox(), await panel.getByRole("button", { name: /Breadth/ }).boundingBox())).toBe(false);
        for (const axis of [
          { name: "Accuracy", label: "生成事实 CLAIM-reset-42" },
          { name: "Breadth", label: "协议覆盖项 REF-BREADTH01" },
          { name: "Depth", label: "因果链节点 REF-DEPTH0001" },
        ] as const) {
          await panel.getByRole("button", { name: `展开 ${axis.name} 详情` }).click();
          await expect(panel.getByText(axis.label)).toBeVisible();
          await panel.scrollIntoViewIfNeeded();
          await page.screenshot({
            path: testInfo.outputPath(
              `f012-blocked-${axis.name.toLowerCase()}-${capture.width}x${capture.height}.png`,
            ),
            fullPage: false,
          });
          await panel.getByRole("button", { name: `收起 ${axis.name} 详情` }).click();
        }
      }
      if (capture.width === 390) {
        const box = await panel.boundingBox();
        expect(box).not.toBeNull();
        expect(box!.y).toBeGreaterThanOrEqual(0);
        expect(box!.y + box!.height).toBeLessThanOrEqual(capture.height);
      }
      await panel.scrollIntoViewIfNeeded();
      await page.screenshot({
        path: testInfo.outputPath(
          `f012-${capture.state}-${capture.width}x${capture.height}.png`,
        ),
        fullPage: false,
      });
    });
  }
});

async function installCockpitFixture(
  page: Parameters<typeof test>[0]["page"],
  state: FixtureState,
  scope: "independent_benchmark" | "operational" = "independent_benchmark",
  options: { pendingResponses?: number } = {},
) {
  const report = evaluationReport(state, scope);
  let qualityRequests = 0;
  await page.route("**/api/workbench/tasks/task_quality_fixture", (route) => route.fulfill({
    json: { task_id: taskId, name: "F012 质量评估夹具", lifecycle_status: "completed" },
  }));
  await page.route("**/api/workbench/task-runs/task_run_quality_fixture/events?**", (route) => route.fulfill({
    json: {
      items: state === "repairing" ? [{
        event_id: 1,
        seq: 1,
        event_kind: "status",
        task_run_id: runId,
        event_type: "stage_progress",
        payload: { kind: "stage_timed_out", stage_id: "business_flow", status: "partial" },
        created_at: "2026-08-03T00:00:10Z",
      }] : [],
      has_older: false,
      latest_event_id: state === "repairing" ? 1 : 0,
    },
  }));
  await page.route("**/api/workbench/task-runs/task_run_quality_fixture/artifacts", (route) => route.fulfill({
    json: { task_run_id: runId, artifact_dir: "/tmp/quality", artifacts: [] },
  }));
  await page.route("**/api/workbench/task-runs/task_run_quality_fixture/quality-evaluation", (route) => {
    qualityRequests += 1;
    if (qualityRequests <= (options.pendingResponses ?? 0)) {
      return route.fulfill({ status: 409, json: { detail: "quality evaluation is incomplete" } });
    }
    if (state === "unavailable") return route.fulfill({ status: 404, json: { detail: "quality evaluation was not found" } });
    return route.fulfill({ json: report });
  });
  await page.route("**/api/workbench/task-runs/task_run_quality_fixture", (route) => route.fulfill({
    json: {
      task_run_id: runId,
      task_id: taskId,
      workflow_id: "workflow-quality",
      workspace_id: "workspace-quality",
      repo_path: "/tmp/quality",
      status: "completed",
      quality_status: state === "repairing" ? "quality_repairing" : "passed",
      delivery_status: "ready",
      runtime: state === "repairing" ? { status: "quality_repairing", quality_repair_attempt: 1, quality_repair_max_attempts: 2 } : { status: "completed" },
      artifact_dir: "/tmp/quality",
      workflow_snapshot: {}, input_snapshot: {}, task_bundle: {}, agent_runs: [], created_at: "2026-08-03T00:00:00Z",
    },
  }));
  return { qualityRequestCount: () => qualityRequests };
}

function evaluationReport(state: FixtureState, scope: "independent_benchmark" | "operational") {
  const finalStatus = state === "blocked" ? "fail" : state === "limited" ? "limited" : "pass";
  const axis = (name: "Accuracy" | "Breadth" | "Depth", status: "pass" | "limited" | "fail") => ({
    status,
    numerator: status === "fail" ? 0 : 1,
    denominator: 1,
    critical_misses: status === "fail" ? [{
      item_id: name === "Accuracy" ? "public-accuracy-a1" : name === "Breadth" ? "public-breadth-b1" : "public-depth-d1",
      public_label: name === "Accuracy" ? "生成事实 CLAIM-reset-42" : name === "Breadth" ? "协议覆盖项 REF-BREADTH01" : "因果链节点 REF-DEPTH0001",
      reason: name === "Accuracy" ? "公开证据未能闭合该事实陈述" : name === "Breadth" ? "关键场景缺少闭环覆盖" : "关键因果链缺少闭环验证",
      recommended_action: name === "Accuracy" ? "核对公开源码证据与事实陈述，并修正不一致内容" : name === "Breadth" ? "补充该关键场景及其对应测试证据" : "补充入口、状态转换、错误传播和验证结果的闭环证据",
      validation_layer: "L2",
    }] : [],
    limitations: state === "limited" ? ["L3_NOT_RUN"] : [],
    validation_layers: {
      L0: { status: "pass", numerator: 1, denominator: 1, critical_miss_ids: [], evidence_refs: [], limitations: [] },
      L1: { status: "pass", numerator: 1, denominator: 1, critical_miss_ids: [], evidence_refs: [], limitations: [] },
      L2: { status: status === "fail" ? "fail" : "pass", numerator: status === "fail" ? 0 : 1, denominator: 1, limitations: [] },
      L3: { status: state === "limited" ? "not_run" : "pass", numerator: state === "limited" ? 0 : 1, denominator: 1, limitations: state === "limited" ? ["L3_NOT_RUN"] : [] },
    },
    metrics: [{ name: name === "Accuracy" ? "claim_precision" : "critical_coverage", numerator: status === "fail" ? 0 : 1, denominator: 1 }],
  });
  const finalSnapshot = {
    accuracy: axis("Accuracy", finalStatus),
    breadth: axis("Breadth", finalStatus),
    depth: axis("Depth", finalStatus),
  };
  const firstStatus = state === "repaired" ? "fail" : finalStatus;
  const firstSnapshot = {
    accuracy: axis("Accuracy", firstStatus),
    breadth: axis("Breadth", firstStatus),
    depth: axis("Depth", firstStatus),
  };
  return {
    schema_version: "quality-evaluation-v1",
    scope,
    run_ref: runId,
    benchmark_identity: scope === "independent_benchmark" ? { case_id: "fixture-case", source_revision: "a".repeat(40), truth_package_version: "1" } : null,
    delivery_status: state === "blocked" ? "not_ready" : state === "limited" ? "limited" : "ready",
    first_pass: firstSnapshot,
    final_after_auto_repair: finalSnapshot,
    repair_summary: { attempt_count: state === "repairing" || state === "repaired" ? 1 : state === "blocked" ? 2 : 0, elapsed_seconds: 32, terminal_block_reason: state === "blocked" ? "修复预算已耗尽" : null, max_attempts: 2, status: state === "repairing" ? "repairing" : "complete" },
    hard_failures: [],
    limitations: state === "limited" ? ["L3_NOT_RUN"] : [],
  };
}

function rectanglesOverlap(
  first: { x: number; y: number; width: number; height: number } | null,
  second: { x: number; y: number; width: number; height: number } | null,
) {
  if (!first || !second) return false;
  return first.x < second.x + second.width
    && first.x + first.width > second.x
    && first.y < second.y + second.height
    && first.y + first.height > second.y;
}
