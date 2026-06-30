import { expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

test("coverage entry discovery renders source file, chain, and input hints", async ({ page }) => {
  const results = [
    {
      module_path: "src/internal.c",
      function_name: "internal_recover",
      kind: "function",
      risk: "high",
      triggered: false,
      hit_count: 0,
      entry_paths: [
        {
          entry_kind: "rpc",
          entry_symbol: "rpc_recover_session",
          entry_file: "src/rpc.c",
          entry_label: "RPC recover-session",
          chain: ["rpc_recover_session", "internal_recover"],
          evidence: "source-backed public RPC path",
          tool: "claude-code",
          provider: "claude-code",
          source_verification: "source_backed",
          input_hints: ["expired auth token"],
        },
      ],
      black_box_cases: [],
      entry_discovery: {
        entry_trace_status: "entry_found",
        candidate_external_entries: [
          {
            entry_type: "rpc",
            entry_symbol: "rpc_recover_session",
            entry_file: "src/rpc.c",
            entry_label: "RPC recover-session",
            external_trigger: "RPC recover-session with invalid TLS PSK",
            chain: ["rpc_recover_session", "internal_recover"],
            evidence: "public RPC handler reaches internal function",
            confidence: "high",
            source_verification: "source_backed",
            provider: "claude-code",
            turn_id: "coverage:src/internal.c:internal_recover:1",
            input_hints: ["invalid TLS PSK", "oversized capsule"],
          },
        ],
        unresolved_reasons: [],
        external_agent: {
          status: "available",
          provider_status: { "claude-code": "ok" },
          warnings: [],
        },
      },
    },
  ];

  await page.route(`${backendBase}/api/coverage/list`, async (route) => {
    await route.fulfill({
      json: [
        {
          id: "cov-entry-ui",
          name: "entry discovery ui",
          status: "analyzed",
          workspace_id: "ws-1",
          source_format: "internal_function_hits",
          created_at: "2026-06-07T00:00:00Z",
        },
      ],
    });
  });
  await page.route(`${backendBase}/api/coverage/cov-entry-ui`, async (route) => {
    await route.fulfill({
      json: {
        id: "cov-entry-ui",
        name: "entry discovery ui",
        status: "analyzed",
        workspace_id: "ws-1",
        source_format: "internal_function_hits",
        analysis_results_json: JSON.stringify(results),
      },
    });
  });
  await page.route(`${backendBase}/api/workspaces`, async (route) => {
    await route.fulfill({ json: [] });
  });

  await page.goto("/coverage", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /internal_recover/ }).click();

  await expect(page.getByText("RPC recover-session", { exact: true })).toBeVisible();
  await expect(page.getByText("trigger: RPC recover-session with invalid TLS PSK")).toBeVisible();
  await expect(page.getByText("src/rpc.c")).toBeVisible();
  await expect(page.getByText("rpc_recover_session → internal_recover").first()).toBeVisible();
  await expect(page.getByText("expired auth token")).toBeVisible();
  await expect(page.getByText("invalid TLS PSK", { exact: true })).toBeVisible();
  await expect(page.getByText("oversized capsule")).toBeVisible();
});

test("coverage page keeps large AI result previews bounded while exports stay complete", async ({
  page,
}) => {
  const results = Array.from({ length: 95 }, (_, index) => ({
    module_path: "lib/nvmf",
    file_path: `lib/nvmf/large_${index}.c`,
    function_name: `gap_${String(index).padStart(3, "0")}`,
    kind: "function",
    risk_level: "medium",
    confidence: "high",
    hit_count: 0,
    line_start: index + 1,
    trigger_branches: Array.from({ length: 14 }, (__, branchIndex) => ({
      source: "function",
      condition: `branch_${branchIndex}`,
      file: `lib/nvmf/large_${index}.c`,
      line_number: branchIndex + 10,
    })),
    entry_paths: Array.from({ length: 10 }, (__, entryIndex) => ({
      entry_kind: "rpc",
      entry_symbol: `rpc_gap_${entryIndex}`,
      chain: [`rpc_gap_${entryIndex}`, `gap_${String(index).padStart(3, "0")}`],
      evidence: `entry evidence ${entryIndex}`,
    })),
    test_scenarios: Array.from({ length: 8 }, (__, scenarioIndex) => ({
      scenario_id: `scenario-${index}-${scenarioIndex}`,
      case_type: "black_box_ready",
      priority: "medium",
      confidence: "high",
      flow_purpose: `scenario ${scenarioIndex}`,
      external_trigger: "public RPC request",
      input_construction: "documented request body",
      normal_path: "request accepted",
      error_path: "request rejected",
      expected_result: "controlled result",
      observable_signals: ["response", "logs"],
    })),
    black_box_cases: Array.from({ length: 12 }, (__, caseIndex) => ({
      title: `case_${caseIndex}`,
      preconditions: "service is running",
      inputs: "public RPC input",
      steps: ["send request", "observe response"],
      expected: "documented response",
      observable_signals: ["response", "logs"],
    })),
    evidence_gaps: Array.from({ length: 10 }, (__, gapIndex) => `gap reason ${gapIndex}`),
  }));

  await page.route(`${backendBase}/api/coverage/list`, async (route) => {
    await route.fulfill({
      json: [
        {
          id: "cov-large-ui",
          name: "large coverage ui",
          status: "analyzed",
          workspace_id: "ws-1",
          source_format: "internal_function_hits",
          created_at: "2026-06-07T00:00:00Z",
        },
      ],
    });
  });
  await page.route(`${backendBase}/api/coverage/cov-large-ui`, async (route) => {
    await route.fulfill({
      json: {
        id: "cov-large-ui",
        name: "large coverage ui",
        status: "analyzed",
        workspace_id: "ws-1",
        source_format: "internal_function_hits",
        analysis_results_json: JSON.stringify(results),
      },
    });
  });
  await page.route(`${backendBase}/api/workspaces`, async (route) => {
    await route.fulfill({ json: [] });
  });

  await page.goto("/coverage", { waitUntil: "domcontentloaded" });

  await expect(page.getByText("页面预览前 80 条 AI 分析结果")).toBeVisible();
  await expect(page.getByRole("button", { name: /gap_/ })).toHaveCount(80);
  await expect(page.getByText("gap_094")).toHaveCount(0);

  await page.getByRole("button", { name: /gap_000/ }).hover();
  await page.getByRole("button", { name: /gap_000/ }).click();
  await expect(page.getByText("case_0", { exact: true })).toBeVisible();
  await expect(page.getByText("case_7", { exact: true })).toBeVisible();
  await expect(page.getByText("case_8", { exact: true })).toHaveCount(0);
  await expect(page.getByText("还有 4 条测试用例未在页面展开")).toBeVisible();
  await expect(page.getByText("还有 2 条测试场景未在页面展开")).toBeVisible();
  await expect(page.getByText("还有 2 条入口路径未在页面展开")).toBeVisible();

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出黑盒用例" }).hover();
  await page.getByRole("button", { name: "导出黑盒用例" }).click();
  const download = await downloadPromise;
  const exportPath = test.info().outputPath("large-black-box-cases.json");
  await download.saveAs(exportPath);
  const exported = JSON.parse(fs.readFileSync(exportPath, "utf8")) as {
    cases: Array<{ function_name: string | null }>;
  };
  expect(exported.cases.some((item) => item.function_name === "gap_094")).toBeTruthy();
});

test("coverage entry discovery is rendered after real workspace upload and AI analysis", async ({
  page,
}) => {
  const suffix = Date.now();
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-entry-ui-")));
  const srcDir = path.join(repo, "src");
  fs.mkdirSync(srcDir, { recursive: true });
  fs.writeFileSync(
    path.join(srcDir, "routes.c"),
    [
      "struct request { int id; void *session; };",
      "struct route_entry { const char *method; const char *path; int (*handler)(struct request *req); };",
      "void cleanup_session(void *s) { (void)s; }",
      "void recover_session(void *s) {",
      "    if (s == 0) {",
      "        return;",
      "    }",
      "    cleanup_session(s);",
      "}",
      "static int handle_recover(struct request *req) {",
      "    if (req->id == 0) {",
      "        return -1;",
      "    }",
      "    recover_session(req->session);",
      "    return 0;",
      "}",
      "static const struct route_entry routes[] = {",
      "    { \"POST\", \"/sessions/{id}/recover\", handle_recover },",
      "};",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `entry-discovery-ws-${suffix}`;
  const analysisName = `entry-discovery-real-${suffix}`;

  await page.goto("/workspaces/new", { waitUntil: "domcontentloaded" });
  await page.getByPlaceholder(/项目 A/).fill(workspaceName);
  await page.getByPlaceholder(/本地文件夹路径/).fill(repo);
  await page.getByRole("button", { name: "创建工作空间" }).hover();
  await page.getByRole("button", { name: "创建工作空间" }).click();
  await page.waitForURL(/\/workspaces\/[0-9a-f-]{36}$/, { timeout: 30_000 });
  await expect(page.getByText(workspaceName)).toBeVisible({ timeout: 30_000 });

  await page.goto("/coverage", { waitUntil: "domcontentloaded" });
  await page.locator('input[type="text"]').first().fill(analysisName);
  await page.locator("select").selectOption({ label: `${workspaceName} - ${repo}` });
  await page.locator('input[type="file"]').setInputFiles({
    name: "entry-discovery-function-hits.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      [
        "feature,module,code_location,function,triggered,hit_count",
        "recover,routes,src/routes.c:4-9,recover_session,false,0",
      ].join("\n"),
      "utf8",
    ),
  });
  await expect(page.getByText("entry-discovery-function-hits.csv")).toBeVisible();
  await page.getByRole("button", { name: "上传并解析" }).hover();
  await page.getByRole("button", { name: "上传并解析" }).click();
  await expect(page.getByText(analysisName)).toBeVisible({ timeout: 15_000 });

  const card = page
    .locator(".bg-surface-container-low")
    .filter({ hasText: analysisName })
    .first();
  await card.getByRole("button", { name: /AI/ }).hover();
  await card.getByRole("button", { name: /AI/ }).click();

  const resultButton = page.getByRole("button", { name: /recover_session/ }).first();
  await expect(resultButton).toBeVisible({ timeout: 20_000 });
  await resultButton.hover();
  await resultButton.click();

  await expect(page.getByText("入口发现")).toBeVisible();
  await expect(page.getByText("已确认外部入口")).toBeVisible();
  await expect(page.getByText("黑盒可触达")).toBeVisible();
  await expect(page.getByText("POST /sessions/{id}/recover").first()).toBeVisible();
  await expect(page.getByText("src/routes.c").first()).toBeVisible();
  await expect(page.getByText(/handle_recover.*recover_session/).first()).toBeVisible();
  await expect(page.getByText("id").first()).toBeVisible();
  await expect(page.getByText("确定性追踪未确认外部入口")).toHaveCount(0);
});
