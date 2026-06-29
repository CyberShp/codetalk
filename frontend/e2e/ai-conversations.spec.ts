import { expect, test } from "@playwright/test";

function jsonHeaders(origin = "http://localhost:3003") {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Content-Type": "application/json",
  };
}

test("AI conversation page is a wide persistent reading surface", async ({ page }) => {
  await page.route("**/api/workspaces", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: [
        {
          id: "ws-1",
          name: "登录项目",
          repo_path: "/repo/login",
          indexed: 1,
          index_job: null,
          index_progress: 100,
          analyze_status: null,
          analyze_progress: 0,
          last_index_error: null,
          created_at: "2026-06-28T00:00:00Z",
          updated_at: "2026-06-28T00:00:00Z",
          materials: [],
          reports: [],
        },
      ],
    });
  });

  await page.route("**/api/ai/conversations?limit=3", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        items: [
          {
            id: "conv-1",
            scope_type: "workspace",
            scope_id: "ws-1",
            workspace_id: "ws-1",
            memory_namespace: "workspace:ws-1",
            title: "登录模块 AI 调查线程",
            status: "idle",
            initial_context: {},
            created_at: "2026-06-28T00:00:00Z",
            updated_at: "2026-06-28T00:00:00Z",
          },
        ],
      },
    });
  });

  await page.route("**/api/ai/conversations?workspace_id=ws-1&limit=50", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        items: [
          {
            id: "conv-1",
            scope_type: "workspace",
            scope_id: "ws-1",
            workspace_id: "ws-1",
            memory_namespace: "workspace:ws-1",
            title: "登录模块 AI 调查线程",
            status: "idle",
            initial_context: {},
            created_at: "2026-06-28T00:00:00Z",
            updated_at: "2026-06-28T00:00:00Z",
          },
        ],
      },
    });
  });

  await page.route("**/api/ai/conversations/conv-1", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        id: "conv-1",
        scope_type: "workspace",
        scope_id: "ws-1",
        workspace_id: "ws-1",
        memory_namespace: "workspace:ws-1",
        title: "登录模块 AI 调查线程",
        status: "idle",
        initial_context: {},
        created_at: "2026-06-28T00:00:00Z",
        updated_at: "2026-06-28T00:00:00Z",
        latest_run: null,
      },
    });
  });

  await page.route("**/api/ai/conversations/conv-1/messages", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        items: [
          {
            id: "msg-1",
            conversation_id: "conv-1",
            run_id: "run-1",
            role: "user",
            content: "这个测试设计还缺什么？",
            references: [],
            actions: [],
            created_at: "2026-06-28T00:00:00Z",
          },
          {
            id: "msg-2",
            conversation_id: "conv-1",
            run_id: "run-1",
            role: "assistant",
            content: "建议补充登录失败、权限失效、弱网重试和审计日志验证。",
            references: [
              {
                source_type: "workspace_report",
                source_id: "report-1",
                title: "测试设计报告",
                excerpt: "报告指出登录流程需要覆盖失败边界和异常路径。",
                metadata: { workspace_id: "ws-1" },
              },
            ],
            actions: [{ id: "save_memory", label: "沉淀到记忆" }],
            created_at: "2026-06-28T00:00:01Z",
          },
        ],
      },
    });
  });

  await page.setViewportSize({ width: 1440, height: 920 });
  await page.goto("/ai/conv-1");

  await expect(page.getByRole("heading", { name: "登录模块 AI 调查线程" })).toBeVisible();
  await expect(page.getByText("建议补充登录失败")).toBeVisible();
  await expect(page.getByText("测试设计报告")).toBeVisible();
  await expect(page.getByPlaceholder(/像 Codex 一样继续追问/)).toBeVisible();

  const reader = page.locator(".ct-codex-ai__reader");
  const readerBox = await reader.boundingBox();
  expect(readerBox?.width ?? 0).toBeGreaterThan(560);
});

test("AI conversation shows latest failed run reason instead of going silent", async ({ page }) => {
  await page.route("**/api/workspaces", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: [
        {
          id: "ws-1",
          name: "登录项目",
          repo_path: "/repo/login",
          indexed: 1,
          index_job: null,
          index_progress: 100,
          analyze_status: null,
          analyze_progress: 0,
          last_index_error: null,
          created_at: "2026-06-28T00:00:00Z",
          updated_at: "2026-06-28T00:00:00Z",
          materials: [],
          reports: [],
        },
      ],
    });
  });

  await page.route("**/api/ai/conversations?workspace_id=ws-1&limit=50", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: { items: [] },
    });
  });

  await page.route("**/api/ai/conversations/conv-error", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        id: "conv-error",
        scope_type: "workspace",
        scope_id: "ws-1",
        workspace_id: "ws-1",
        memory_namespace: "workspace:ws-1",
        title: "登录模块 AI 调查线程",
        status: "error",
        initial_context: {},
        created_at: "2026-06-28T00:00:00Z",
        updated_at: "2026-06-28T00:00:00Z",
        latest_run: {
          id: "run-error",
          conversation_id: "conv-error",
          status: "failed",
          cursor: 1,
          error: "LLM 不可用：未配置活跃的聊天模型，请先在设置中选择 LLM 模型",
          model: null,
          token_usage: {},
          created_at: "2026-06-28T00:00:01Z",
          started_at: null,
          completed_at: "2026-06-28T00:00:02Z",
        },
      },
    });
  });

  await page.route("**/api/ai/conversations/conv-error/messages", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        items: [
          {
            id: "msg-user",
            conversation_id: "conv-error",
            run_id: "run-error",
            role: "user",
            content: "为什么没有回复？",
            references: [],
            actions: [],
            created_at: "2026-06-28T00:00:01Z",
          },
        ],
      },
    });
  });

  await page.goto("/ai/conv-error");

  await expect(page.locator(".ct-codex-ai__error")).toContainText("未配置活跃的聊天模型");
  await expect(page.getByRole("link", { name: "去设置执行器" })).toHaveAttribute("href", "/settings");
});
