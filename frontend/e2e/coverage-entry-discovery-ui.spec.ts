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
