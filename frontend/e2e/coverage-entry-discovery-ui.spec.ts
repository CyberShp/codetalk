import { expect, test } from "@playwright/test";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "8100"}`;

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

  await expect(page.getByText("RPC recover-session")).toBeVisible();
  await expect(page.getByText("src/rpc.c")).toBeVisible();
  await expect(page.getByText("rpc_recover_session → internal_recover").first()).toBeVisible();
  await expect(page.getByText("expired auth token")).toBeVisible();
  await expect(page.getByText("invalid TLS PSK")).toBeVisible();
  await expect(page.getByText("oversized capsule")).toBeVisible();
});
