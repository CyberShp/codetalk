import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const apiKey = process.env.CODETALK_E2E_LLM_API_KEY || process.env.DEEPSEEK_API_KEY || "";
const dataDir = process.env.CODETALK_PLAYWRIGHT_DATA_DIR || "";
const spdkRepo = process.env.CODETALK_E2E_REPO || "/Volumes/Media/dpdk/spdk";

test.skip(!apiKey, "CODETALK_E2E_LLM_API_KEY or DEEPSEEK_API_KEY is required");
test.skip(!fs.existsSync(spdkRepo), `SPDK repository is unavailable: ${spdkRepo}`);
test.skip(!dataDir, "CODETALK_PLAYWRIGHT_DATA_DIR is required for metric collection");

test("runs the real SPDK staged workflow five times with bounded business-flow governance", async ({ page }) => {
  test.setTimeout(55 * 60_000);
  const stamp = Date.now();
  const workspaceName = `SPDK regular-stage ${stamp}`;
  const taskName = `SPDK iSCSI login governance ${stamp}`;
  const target = [
    "基于 SPDK 当前源码分析 iSCSI login 主流程、CHAP 认证、digest 协商、错误清理与 session 恢复。",
    "所有结论必须引用真实文件、符号和测试目录证据，输出代码证据、流程、SFMEA 与外部可执行黑盒用例。",
  ].join("\n");
  const metrics: Array<Record<string, unknown>> = [];

  await page.setViewportSize({ width: 1440, height: 900 });
  await configureDeepSeekThroughUi(page, `DeepSeek governance ${stamp}`);
  await createWorkspaceThroughUi(page, workspaceName);
  const firstRun = await createAndRunTaskThroughUi(page, {
    taskName,
    workspaceName,
    target,
  });
  const taskId = firstRun.taskId;
  let clickedAt = firstRun.clickedAt;

  for (let attempt = 1; attempt <= 5; attempt += 1) {
    if (attempt > 1) {
      await page.goto(`/tasks/${taskId}`, { waitUntil: "domcontentloaded" });
      const start = page.getByRole("button", { name: "启动新运行" });
      await start.hover();
      clickedAt = Date.now();
      await start.click();
      await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}/runs/task_run_`));
    }
    const runId = page.url().split("/").pop() || "";
    expect(runId).toMatch(/^task_run_/);

    if (attempt === 1) {
      const progress = page.locator(".ct-v2-stage-progress");
      await expect(progress.getByText("业务流程", { exact: true })).toBeVisible({
        timeout: 12 * 60_000,
      });
      await expect(progress).not.toContainText("business_flow · running");
      await expect(progress).toContainText(/已收到首段输出|当前已生成|阶段已完成|已保留部分结果/, {
        timeout: 6 * 60_000,
      });
      await progress.screenshot({
        path: path.join(dataDir, "regular-stage-business-flow-running.png"),
      });
    }

    const status = page.locator(".ct-v2-run-status").filter({ hasText: "执行状态" }).locator("strong");
    await expect.poll(async () => (await status.textContent())?.trim(), {
      timeout: attempt === 1 ? 25 * 60_000 : 12 * 60_000,
      intervals: [1000, 2000, 5000],
    }).toMatch(/^(已完成|部分完成|失败|已取消|已中断)$/);
    await expect(status).toHaveText(/^(已完成|部分完成)$/);
    const terminalStatus = (await status.textContent())?.trim() || "";
    const terminalAt = Date.now();
    await page.screenshot({
      path: path.join(dataDir, `regular-stage-attempt-${attempt}-completed.png`),
      fullPage: false,
    });

    const metric = collectRunMetric({ runId, attempt, clickedAt, terminalAt, terminalStatus });
    metrics.push(metric);
    expect(Number(metric.click_to_run_terminal_ms)).toBeLessThanOrEqual(20 * 60_000);
    expect(Number(metric.flow_evidence_duration_ms)).toBeLessThanOrEqual(45_000);
    expect(Number(metric.business_flow_total_duration_ms)).toBeLessThanOrEqual(360_000);
    if (!metric.business_flow_reused) {
      expect(Number(metric.business_flow_time_to_first_token_ms)).toBeLessThanOrEqual(60_000);
      expect(Number(metric.business_flow_output_tokens)).toBeGreaterThan(100);
    }
  }

  expect(metrics).toHaveLength(5);
  expect(metrics.slice(1).some((item) => item.flow_evidence_cache_status === "hit")).toBeTruthy();
  const output = path.join(dataDir, "regular-stage-governance-metrics.json");
  fs.writeFileSync(output, JSON.stringify({ target, spdkRepo, metrics }, null, 2), "utf8");
});

async function configureDeepSeekThroughUi(page: import("@playwright/test").Page, configName: string) {
  await page.goto("/settings", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /可选：内置模型与 RAG 检索/ }).click();
  await page.getByRole("button", { name: "新增" }).click();
  const form = page.locator("form").filter({ hasText: "新增 LLM 配置" });
  await form.getByPlaceholder("如：Claude / GPT-4o").fill(configName);
  await form.getByPlaceholder("https://api.openai.com/v1").fill("https://api.deepseek.com");
  await form.getByPlaceholder(/sk-|Ollama/).fill(apiKey);
  await form.getByRole("textbox", { name: "gpt-4o", exact: true }).fill("deepseek-chat");
  await form.getByRole("button", { name: "测试连接" }).hover();
  await form.getByRole("button", { name: "测试连接" }).click();
  await expect(form).toContainText(/连接成功|测试成功|模型响应正常/, { timeout: 90_000 });
  await form.getByRole("button", { name: "保存配置" }).click();
  await expect(page.getByText(configName, { exact: true })).toBeVisible();
  await expect(page.locator("body")).not.toContainText(apiKey);
}

async function createWorkspaceThroughUi(page: import("@playwright/test").Page, workspaceName: string) {
  await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
  await page.getByPlaceholder(/项目 A/).fill(workspaceName);
  await page.getByPlaceholder(/本地文件夹路径/).fill(spdkRepo);
  const create = page.getByRole("button", { name: "创建工作空间" });
  await create.hover();
  await create.click();
  await page.waitForURL(/\/workspaces\/[0-9a-f-]{36}$/, { timeout: 60_000 });
  await expect(page.getByText(workspaceName, { exact: true })).toBeVisible();
}

async function createAndRunTaskThroughUi(
  page: import("@playwright/test").Page,
  values: { taskName: string; workspaceName: string; target: string },
) {
  await page.goto("/tasks/new", { waitUntil: "domcontentloaded" });
  const workflow = page.locator(".ct-v2-workflow-choice label").filter({ hasText: /SFMEA/ }).first();
  await expect(workflow).toBeVisible({ timeout: 30_000 });
  await workflow.getByRole("radio").check();
  await page.getByRole("button", { name: "保存并继续" }).click();

  await page.getByRole("textbox", { name: "任务名称 *" }).fill(values.taskName);
  await page.getByLabel("工作空间 *").selectOption({ label: values.workspaceName });
  await page.getByRole("textbox", { name: "描述" }).fill("真实 SPDK 五次 Regular Stage 性能与恢复验收");
  await page.getByRole("button", { name: "保存并继续" }).click();

  const analysisObject = page
    .locator(".ct-v2-dynamic-inputs label")
    .filter({ hasText: "analysis_object" })
    .locator("textarea, input:not([type=file])")
    .first();
  const targetValue = values.target.replace(/\s+/g, " ").trim();
  await expect(analysisObject).toBeVisible();
  await analysisObject.fill(targetValue);
  await expect(analysisObject).toHaveValue(targetValue);
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.getByRole("heading", { name: "确认执行配置" })).toBeVisible();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.getByRole("heading", { name: "确认交付输出" })).toBeVisible();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.getByRole("heading", { name: "检查并运行" })).toBeVisible();
  await page.getByRole("button", { name: "保存为就绪任务" }).click();
  await expect(page).toHaveURL(/\/tasks\/task_/);
  const taskId = page.url().split("/").pop() || "";
  const start = page.getByRole("button", { name: "启动新运行" });
  await start.hover();
  const clickedAt = Date.now();
  await start.click();
  await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}/runs/task_run_`));
  return { taskId, clickedAt };
}

function collectRunMetric({
  runId,
  attempt,
  clickedAt,
  terminalAt,
  terminalStatus,
}: {
  runId: string;
  attempt: number;
  clickedAt: number;
  terminalAt: number;
  terminalStatus: string;
}) {
  const runRoot = path.join(dataDir, "workbench", "task_runs", runId);
  const flowEvidenceResult = readJson(findFile(runRoot, "/stages/flow_evidence_pack/stage_result.json"));
  const businessFlowResult = readJson(findFile(runRoot, "/stages/business_flow/stage_result.json"));
  const sfmeaResult = readJson(findFile(runRoot, "/stages/sfmea/stage_result.json"));
  const blackBoxResult = readJson(findFile(runRoot, "/stages/black_box_cases/stage_result.json"));
  const qualityAudit = readJson(findFile(runRoot, "/test_activity_quality_audit.json"));
  const eventsPath = path.join(runRoot, "task_run_events.jsonl");
  const events = fs.existsSync(eventsPath)
    ? fs.readFileSync(eventsPath, "utf8").split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line))
    : [];
  const stageEvents = events.map((item) => item.payload || item).filter((item) => String(item.kind || "").startsWith("stage_"));
  const timestamp = (kind: string, stageId = "") => {
    const event = events.find((item) => {
      const payload = item.payload || item;
      return payload.kind === kind && (!stageId || payload.stage_id === stageId);
    });
    return event?.created_at ? new Date(event.created_at).getTime() : 0;
  };
  const firstTimestamp = (kinds: string[], stageId: string) => {
    const values = kinds.map((kind) => timestamp(kind, stageId)).filter((value) => value > 0);
    return values.length ? Math.min(...values) : 0;
  };
  return {
    attempt,
    run_id: runId,
    terminal_status: terminalStatus,
    click_to_run_terminal_ms: Math.max(0, terminalAt - clickedAt),
    click_to_flow_evidence_ready_ms: Math.max(0, timestamp("stage_flow_evidence_ready") - clickedAt),
    click_to_flow_outline_complete_ms: Math.max(
      0,
      firstTimestamp(["stage_completed", "stage_reused"], "flow_outline") - clickedAt,
    ),
    click_to_business_flow_first_token_ms: Math.max(0, timestamp("stage_first_token", "business_flow") - clickedAt),
    click_to_business_flow_complete_ms: Math.max(
      0,
      firstTimestamp(["stage_completed", "stage_reused"], "business_flow") - clickedAt,
    ),
    flow_evidence_duration_ms: Number(flowEvidenceResult.total_duration_ms || 0),
    flow_evidence_cache_status: String(flowEvidenceResult.cache_status || ""),
    business_flow_total_duration_ms: Number(businessFlowResult.total_duration_ms || 0),
    business_flow_time_to_first_token_ms: Number(businessFlowResult.time_to_first_token_ms || 0),
    business_flow_provider_wait_ms: Number(businessFlowResult.provider_wait_ms || 0),
    business_flow_prompt_tokens: Number(businessFlowResult.prompt_estimated_tokens || 0),
    business_flow_output_tokens: Number(businessFlowResult.output_tokens || 0),
    business_flow_attempt_count: Number(businessFlowResult.attempt_count || 0),
    business_flow_finish_reason: String(businessFlowResult.finish_reason || ""),
    business_flow_cache_status: String(businessFlowResult.cache_status || ""),
    business_flow_reused: Boolean(businessFlowResult.reused),
    business_flow_degraded: Boolean(businessFlowResult.degraded),
    sfmea_total_duration_ms: Number(sfmeaResult.total_duration_ms || 0),
    sfmea_provider_wait_ms: Number(sfmeaResult.provider_wait_ms || 0),
    sfmea_attempt_count: Number(sfmeaResult.attempt_count || 0),
    sfmea_cache_status: String(sfmeaResult.cache_status || ""),
    sfmea_reused: Boolean(sfmeaResult.reused),
    black_box_total_duration_ms: Number(blackBoxResult.total_duration_ms || 0),
    black_box_provider_wait_ms: Number(blackBoxResult.provider_wait_ms || 0),
    black_box_attempt_count: Number(blackBoxResult.attempt_count || 0),
    black_box_cache_status: String(blackBoxResult.cache_status || ""),
    black_box_reused: Boolean(blackBoxResult.reused),
    quality_status: String(qualityAudit.status || ""),
    quality_issue_count: Array.isArray(qualityAudit.issues) ? qualityAudit.issues.length : 0,
    public_stage_event_count: stageEvents.length,
  };
}

function findFile(root: string, suffix: string): string {
  if (!fs.existsSync(root)) throw new Error(`Run artifact root not found: ${root}`);
  const queue = [root];
  while (queue.length) {
    const current = queue.shift()!;
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const candidate = path.join(current, entry.name);
      if (entry.isDirectory()) queue.push(candidate);
      else if (candidate.replaceAll("\\", "/").endsWith(suffix)) return candidate;
    }
  }
  throw new Error(`Artifact not found under ${root}: ${suffix}`);
}

function readJson(filePath: string): Record<string, unknown> {
  return JSON.parse(fs.readFileSync(filePath, "utf8")) as Record<string, unknown>;
}
