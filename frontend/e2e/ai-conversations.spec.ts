import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import fs from "node:fs";

const frontendOrigin = `http://localhost:${process.env.CODETALK_FRONTEND_PORT ?? "3003"}`;

function jsonHeaders(origin = frontendOrigin) {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Content-Type": "application/json",
  };
}

async function mockReadableConversation(
  page: Page,
  options: {
    assistantContent?: string;
    extraMessages?: Array<Record<string, unknown>>;
  } = {},
) {
  const assistantContent =
    options.assistantContent ?? "建议补充登录失败、权限失效、弱网重试和审计日志验证。";
  const references = [
    {
      source_type: "workspace_report",
      source_id: "report-1",
      title: "测试设计报告",
      excerpt: "报告指出登录流程需要覆盖失败边界和异常路径。",
      metadata: { workspace_id: "ws-1" },
    },
    {
      source_type: "workspace_source",
      source_id: "src-1",
      title: "lib/login/session.ts:42",
      excerpt: "会话过期时返回 401 并记录审计日志。",
      metadata: { workspace_id: "ws-1" },
    },
    {
      source_type: "workspace_material",
      source_id: "mat-1",
      title: "登录验收标准.md",
      excerpt: "弱网、重试、权限失效均为验收范围。",
      metadata: { workspace_id: "ws-1" },
    },
    {
      source_type: "semantic_case",
      source_id: "case-1",
      title: "历史弱网案例",
      excerpt: "历史案例要求观察重试次数和最终错误提示。",
      metadata: { workspace_id: "ws-1" },
    },
    {
      source_type: "workspace_report",
      source_id: "report-2",
      title: "审计日志报告",
      excerpt: "审计日志应包含登录失败原因。",
      metadata: { workspace_id: "ws-1" },
    },
  ];

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
            content: assistantContent,
            references,
            actions: [{ id: "save_memory", label: "沉淀到记忆" }],
            created_at: "2026-06-28T00:00:01Z",
          },
          ...(options.extraMessages ?? []),
        ],
      },
    });
  });
}

test("AI conversation page is a wide persistent reading surface", async ({ page }, testInfo) => {
  await mockReadableConversation(page);
  await page.setViewportSize({ width: 1440, height: 920 });
  await page.goto("/ai/conv-1");

  await expect(page.getByRole("heading", { name: "登录模块 AI 调查线程" })).toBeVisible();
  await expect(page.getByText("建议补充登录失败")).toBeVisible();
  await expect(page.getByText("测试设计报告")).toBeVisible();
  await expect(page.getByText("证据链")).toBeVisible();
  await expect(page.getByText("执行轨迹")).toBeVisible();
  await expect(page.getByText("展开其余 1 条证据")).toBeVisible();
  await expect(page.getByText("审计日志应包含登录失败原因。")).toBeHidden();
  await expect(page.getByText("诊断详情：默认折叠")).toBeVisible();
  await expect(page.getByPlaceholder(/像 Codex 一样继续追问/)).toBeVisible();

  const reader = page.locator(".ct-codex-ai__reader");
  const readerBox = await reader.boundingBox();
  expect(readerBox?.width ?? 0).toBeGreaterThan(560);

  const density = await page.locator(".ct-codex-message__content > div").first().evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const styles = window.getComputedStyle(element);
    return {
      fontSize: Number.parseFloat(styles.fontSize),
      lineHeight: Number.parseFloat(styles.lineHeight),
      width: rect.width,
      paddingTop: Number.parseFloat(styles.paddingTop),
      paddingLeft: Number.parseFloat(styles.paddingLeft),
    };
  });
  expect(density.fontSize).toBeGreaterThanOrEqual(14);
  expect(density.fontSize).toBeLessThanOrEqual(16);
  expect(density.lineHeight / density.fontSize).toBeLessThanOrEqual(1.7);
  expect(density.width).toBeLessThanOrEqual(760);
  expect(density.paddingTop).toBeLessThanOrEqual(14);
  expect(density.paddingLeft).toBeLessThanOrEqual(16);

  const composerFontSize = await page.locator(".ct-codex-composer textarea").evaluate((element) =>
    Number.parseFloat(window.getComputedStyle(element).fontSize),
  );
  expect(composerFontSize).toBeGreaterThanOrEqual(14);
  expect(composerFontSize).toBeLessThanOrEqual(16);

  const topbarLayout = await page.locator(".ct-codex-ai__topbar > *").evaluateAll((nodes) => {
    const boxes = nodes.map((node) => {
      const rect = node.getBoundingClientRect();
      return {
        left: rect.left,
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
        width: rect.width,
        height: rect.height,
      };
    });
    const overlaps: string[] = [];
    const sameRowGaps: number[] = [];
    for (let i = 0; i < boxes.length; i += 1) {
      for (let j = i + 1; j < boxes.length; j += 1) {
        const a = boxes[i];
        const b = boxes[j];
        const xOverlap = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
        const yOverlap = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
        if (xOverlap * yOverlap > 1) overlaps.push(`${i}:${j}`);
        if (yOverlap > Math.min(a.height, b.height) * 0.5 && b.left >= a.right) {
          sameRowGaps.push(Math.round(b.left - a.right));
        }
      }
    }
    return { overlaps, sameRowGaps };
  });
  expect(topbarLayout.overlaps).toEqual([]);
  expect(topbarLayout.sameRowGaps.every((gap) => gap >= 8)).toBe(true);

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/登录模块-AI-调查线程-conv-1\.md$/);
  const exportPath = testInfo.outputPath("ai-thread-export.md");
  await download.saveAs(exportPath);
  const exported = fs.readFileSync(exportPath, "utf8");
  expect(exported).toContain("# 登录模块 AI 调查线程");
  expect(exported).toContain("这个测试设计还缺什么？");
  expect(exported).toContain("建议补充登录失败、权限失效、弱网重试和审计日志验证。");
  expect(exported).toContain("测试设计报告 (workspace_report:report-1)");
});

test("AI conversation page skips decorative atmosphere layers for tool performance", async ({ page }) => {
  await mockReadableConversation(page);
  await page.setViewportSize({ width: 1440, height: 920 });
  await page.goto("/ai/conv-1", { waitUntil: "domcontentloaded" });

  await expect(page.getByRole("heading", { name: "登录模块 AI 调查线程" })).toBeVisible();
  await expect(page.locator(".ct-atmosphere")).toHaveCount(0);
});

test("AI conversation keeps long threads inside the reader and does not force document scrolling", async ({ page }) => {
  const longBlock = Array.from({ length: 14 }, (_, index) =>
    `第 ${index + 1} 段：补充登录失败、权限失效、弱网重试、审计日志验证和恢复路径。`,
  ).join("\n\n");
  const extraMessages = Array.from({ length: 5 }, (_, index) => ({
    id: `msg-extra-${index}`,
    conversation_id: "conv-1",
    run_id: "run-1",
    role: index % 2 === 0 ? "user" : "assistant",
    content: `${index % 2 === 0 ? "继续追问" : longBlock}\n${index}`,
    references: [],
    actions: [],
    created_at: `2026-06-28T00:00:${10 + index}Z`,
  }));
  await mockReadableConversation(page, { assistantContent: longBlock, extraMessages });
  await page.setViewportSize({ width: 1440, height: 760 });
  await page.goto("/ai/conv-1", { waitUntil: "domcontentloaded" });

  const metrics = await page.getByLabel("AI 线程对话内容").evaluate((element) => ({
    readerClientHeight: element.clientHeight,
    readerScrollHeight: element.scrollHeight,
    documentScrollHeight: document.documentElement.scrollHeight,
    viewportHeight: window.innerHeight,
    scrollBehavior: window.getComputedStyle(element).scrollBehavior,
    overscrollBehavior: window.getComputedStyle(element).overscrollBehavior,
  }));
  expect(metrics.readerScrollHeight).toBeGreaterThan(metrics.readerClientHeight + 300);
  expect(metrics.documentScrollHeight).toBeLessThanOrEqual(metrics.viewportHeight + 24);
  expect(metrics.scrollBehavior).not.toBe("smooth");
  expect(metrics.overscrollBehavior).toBe("contain");

  const readerBox = await page.getByLabel("AI 线程对话内容").boundingBox();
  expect(readerBox).not.toBeNull();
  await page.mouse.move(readerBox!.x + readerBox!.width / 2, readerBox!.y + readerBox!.height / 2);
  await page.mouse.wheel(0, 800);
  await expect
    .poll(() => page.getByLabel("AI 线程对话内容").evaluate((element) => element.scrollTop))
    .toBeGreaterThan(100);
});

test("AI conversation keeps generation diagnostics collapsed outside the answer body", async ({ page }) => {
  let completed = false;
  await page.route("**/api/workspaces", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: [
        {
          id: "ws-diag",
          name: "诊断项目",
          repo_path: "/repo/diag",
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
  await page.route("**/api/settings/agent-runtimes?enabled=true", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [] } });
  });
  await page.route("**/api/ai/conversations?workspace_id=ws-diag&limit=50", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [] } });
  });
  await page.route("**/api/ai/conversations/conv-diag", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        id: "conv-diag",
        scope_type: "workspace",
        scope_id: "ws-diag",
        workspace_id: "ws-diag",
        memory_namespace: "workspace:ws-diag",
        title: "诊断折叠线程",
        status: completed ? "idle" : "running",
        initial_context: {},
        created_at: "2026-06-28T00:00:00Z",
        updated_at: "2026-06-28T00:00:00Z",
        latest_run: {
          id: "run-diag",
          conversation_id: "conv-diag",
          status: completed ? "completed" : "running",
          cursor: completed ? 3 : 0,
          error: null,
          model: "test",
          token_usage: {},
          created_at: "2026-06-28T00:00:01Z",
          started_at: "2026-06-28T00:00:01Z",
          completed_at: completed ? "2026-06-28T00:00:03Z" : null,
        },
      },
    });
  });
  await page.route("**/api/ai/conversations/conv-diag/messages", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        items: [
          {
            id: "msg-diag-user",
            conversation_id: "conv-diag",
            run_id: "run-diag",
            role: "user",
            content: "分析 reconnect timeout",
            references: [],
            actions: [],
            created_at: "2026-06-28T00:00:01Z",
          },
          ...(completed
            ? [
                {
                  id: "msg-diag-assistant",
                  conversation_id: "conv-diag",
                  run_id: "run-diag",
                  role: "assistant",
                  content: "最终答案：覆盖 reconnect timeout 的黑盒观察点。",
                  references: [],
                  actions: [],
                  created_at: "2026-06-28T00:00:03Z",
                },
              ]
            : []),
        ],
      },
    });
  });
  await page.route("**/api/ai/conversations/conv-diag/stream?cursor=0", async (route) => {
    completed = true;
    await route.fulfill({
      headers: {
        ...jsonHeaders(route.request().headers().origin),
        "Content-Type": "text/event-stream",
      },
      body: [
        'data: {"event_id":1,"run_id":"run-diag","conversation_id":"conv-diag","event_type":"status","payload":{"status":"running","message":"正在准备工作区源码上下文"},"created_at":"2026-06-28T00:00:01Z"}',
        "",
        'data: {"event_id":2,"run_id":"run-diag","conversation_id":"conv-diag","event_type":"delta","payload":{"kind":"diagnostic","content":"正在读取 lib/nvmf/connect.c"},"created_at":"2026-06-28T00:00:01Z"}',
        "",
        'data: {"event_id":3,"run_id":"run-diag","conversation_id":"conv-diag","event_type":"delta","payload":{"content":"最终答案：覆盖 reconnect timeout 的黑盒观察点。"},"created_at":"2026-06-28T00:00:02Z"}',
        "",
        'data: {"event_id":4,"run_id":"run-diag","conversation_id":"conv-diag","event_type":"done","payload":{},"created_at":"2026-06-28T00:00:03Z"}',
        "",
      ].join("\n"),
    });
  });

  await page.goto("/ai/conv-diag", { waitUntil: "domcontentloaded" });

  await expect(page.getByText("最终答案：覆盖 reconnect timeout 的黑盒观察点。")).toBeVisible();
  await expect(page.locator(".ct-codex-ai__reader")).not.toContainText("正在准备工作区源码上下文");
  await expect(page.locator(".ct-codex-ai__reader")).not.toContainText("正在读取 lib/nvmf/connect.c");
  await expect(page.getByText("生成诊断：默认折叠")).toBeVisible();
  await expect(page.getByText("正在准备工作区源码上下文")).toBeHidden();
  await expect(page.getByText("正在读取 lib/nvmf/connect.c")).toBeHidden();
  await page.getByText("生成诊断：默认折叠").click();
  await expect(page.getByText("正在准备工作区源码上下文")).toBeVisible();
  await expect(page.getByText("正在读取 lib/nvmf/connect.c")).toBeVisible();
});

test("AI conversation remains usable on a narrow mobile viewport", async ({ page }) => {
  await mockReadableConversation(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/ai/conv-1");

  await expect(page.getByRole("heading", { name: "登录模块 AI 调查线程" })).toBeVisible();
  await expect(page.getByText("建议补充登录失败")).toBeVisible();
  await expect(page.getByPlaceholder(/像 Codex 一样继续追问/)).toBeVisible();

  const layout = await page.locator(".ct-codex-ai").evaluate((element) => {
    const app = element.getBoundingClientRect();
    const main = element.querySelector(".ct-codex-ai__main")!.getBoundingClientRect();
    const reader = element.querySelector(".ct-codex-ai__reader")!.getBoundingClientRect();
    const composer = element.querySelector(".ct-codex-composer")!.getBoundingClientRect();
    const message = element.querySelector(".ct-codex-message__content > div")!.getBoundingClientRect();
    const messageStyles = window.getComputedStyle(element.querySelector(".ct-codex-message__content > div")!);
    const textareaStyles = window.getComputedStyle(element.querySelector(".ct-codex-composer textarea")!);
    const nodes = Array.from(element.querySelectorAll(".ct-codex-ai__topbar button, .ct-codex-ai__topbar select, .ct-codex-composer button"));
    const boxes = nodes.map((node) => {
      const rect = node.getBoundingClientRect();
      return { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height };
    });
    const overflows = [
      ["main", main],
      ["reader", reader],
      ["composer", composer],
      ["message", message],
    ]
      .filter(([, rect]) => {
        const box = rect as DOMRect;
        return box.left < app.left - 1 || box.right > app.right + 1;
      })
      .map(([name]) => name);
    const overlaps: string[] = [];
    for (let i = 0; i < boxes.length; i += 1) {
      for (let j = i + 1; j < boxes.length; j += 1) {
        const a = boxes[i];
        const b = boxes[j];
        const x = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
        const y = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
        if (x * y > 20) overlaps.push(`${i}:${j}`);
      }
    }
    return {
      overflows,
      overlaps,
      messageFontSize: Number.parseFloat(messageStyles.fontSize),
      textareaFontSize: Number.parseFloat(textareaStyles.fontSize),
      composerWidth: composer.width,
      appWidth: app.width,
    };
  });

  expect(layout.overflows).toEqual([]);
  expect(layout.overlaps).toEqual([]);
  expect(layout.messageFontSize).toBeGreaterThanOrEqual(14);
  expect(layout.messageFontSize).toBeLessThanOrEqual(16);
  expect(layout.textareaFontSize).toBeGreaterThanOrEqual(14);
  expect(layout.textareaFontSize).toBeLessThanOrEqual(16);
  expect(layout.composerWidth).toBeLessThanOrEqual(layout.appWidth);
  await page.getByRole("button", { name: "环境" }).click();
  await expect(page.getByRole("heading", { name: "环境信息" })).toHaveCount(0);
});

test("AI conversation shows latest failed run reason instead of going silent", async ({ page }) => {
  let retryPosted = false;
  const secret = "agent-export-secret-value";
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
          error: `LLM 不可用：未配置活跃的聊天模型，请先在设置中选择 LLM 模型；token=<redacted>`,
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
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as { content?: string };
      retryPosted = body.content === "为什么没有回复？";
      await route.fulfill({
        headers: jsonHeaders(route.request().headers().origin),
        json: {
          message: {
            id: "msg-retry",
            conversation_id: "conv-error",
            run_id: "run-retry",
            role: "user",
            content: body.content,
            references: [],
            actions: [],
            created_at: "2026-06-28T00:00:03Z",
          },
          run: {
            id: "run-retry",
            conversation_id: "conv-error",
            status: "running",
            cursor: 0,
            error: null,
            model: "test",
            token_usage: {},
            created_at: "2026-06-28T00:00:03Z",
            started_at: "2026-06-28T00:00:03Z",
            completed_at: null,
          },
          references: [],
        },
      });
      return;
    }
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
          ...(retryPosted
            ? [
                {
                  id: "msg-assistant-retry",
                  conversation_id: "conv-error",
                  run_id: "run-retry",
                  role: "assistant",
                  content: "重试已启动。",
                  references: [],
                  actions: [],
                  created_at: "2026-06-28T00:00:05Z",
                },
              ]
            : []),
        ],
      },
    });
  });
  await page.route("**/api/ai/conversations/conv-error/stream?cursor=0", async (route) => {
    await route.fulfill({
      headers: {
        ...jsonHeaders(route.request().headers().origin),
        "Content-Type": "text/event-stream",
      },
      body: [
        'data: {"event_id":2,"run_id":"run-retry","conversation_id":"conv-error","event_type":"delta","payload":{"content":"重试已启动。"},"created_at":"2026-06-28T00:00:04Z"}',
        "",
        'data: {"event_id":3,"run_id":"run-retry","conversation_id":"conv-error","event_type":"done","payload":{},"created_at":"2026-06-28T00:00:05Z"}',
        "",
      ].join("\n"),
    });
  });

  await page.goto("/ai/conv-error");

  await expect(page.locator(".ct-codex-ai__error")).toContainText("未配置活跃的聊天模型");
  await expect(page.locator(".ct-codex-ai__error")).not.toContainText(secret);

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出" }).click();
  const download = await downloadPromise;
  const exportPath = test.info().outputPath("ai-thread-failed-export.md");
  await download.saveAs(exportPath);
  const exported = fs.readFileSync(exportPath, "utf8");
  expect(exported).toContain("## 最近失败");
  expect(exported).toContain("未配置活跃的聊天模型");
  expect(exported).toContain("<redacted>");
  expect(exported).not.toContain(secret);

  await page.getByRole("button", { name: "重试上一条" }).click();
  await expect.poll(() => retryPosted).toBe(true);
  await expect(page.getByText("重试已启动。")).toBeVisible();
  await expect(page.getByRole("link", { name: "去设置执行器" })).toHaveAttribute("href", "/settings");
});
