import { expect, test } from "@playwright/test";
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

test("AI conversation page is a wide persistent reading surface", async ({ page }, testInfo) => {
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
