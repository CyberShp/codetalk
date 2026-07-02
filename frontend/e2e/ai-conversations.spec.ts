import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import fs from "node:fs";
import http from "node:http";
import type { AddressInfo } from "node:net";

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
    extraReferences?: Array<Record<string, unknown>>;
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
      metadata: {
        workspace_id: "ws-1",
        path: "lib/login/session.ts",
        start_line: 42,
        end_line: 88,
      },
    },
    {
      source_type: "workbench_task_artifact",
      source_id: "run-spdk-001/task_artifact_manifest.json",
      title: "task_artifact_manifest.json",
      excerpt: "任务产物包含 flow.md、sfmea.md、blackbox_cases.md。",
      metadata: {
        workspace_id: "ws-1",
        task_run_id: "run-spdk-001",
        path: "/tmp/codetalk-e2e-spdk/run-spdk-001/task_artifact_manifest.json",
      },
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
    ...(options.extraReferences ?? []),
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
  await expect(page.locator(".ct-codex-ai__project small")).toHaveText("workspace:ws-1");
  await expect(page.getByText("/repo/login")).toHaveCount(0);
  await expect(page.getByText("建议补充登录失败")).toBeVisible();
  await expect(page.getByText("测试设计报告")).toBeVisible();
  await expect(page.getByText("证据链")).toBeVisible();
  await expect(page.getByText("源码位置")).toBeVisible();
  await expect(page.getByText("lib/login/session.ts:L42-L88")).toBeVisible();
  await expect(page.getByText("任务产物", { exact: true })).toBeVisible();
  await expect(page.getByText("run-spdk-001 · task_artifact_manifest.json")).toBeVisible();
  await expect(page.getByRole("link", { name: "打开产物" })).toHaveAttribute(
    "href",
    "/api/workbench/task-runs/run-spdk-001/artifacts/content/task_artifact_manifest.json",
  );
  await expect(page.getByText("执行轨迹")).toBeVisible();
  await expect(page.getByText("展开其余 2 条证据")).toBeVisible();
  await expect(page.getByText("审计日志应包含登录失败原因。")).toBeHidden();
  await expect(page.getByText("诊断详情：默认折叠")).toBeVisible();
  await expect(page.getByPlaceholder(/像 Codex 一样继续追问/)).toBeVisible();

  const reader = page.locator(".ct-codex-ai__reader");
  const readerBox = await reader.boundingBox();
  expect(readerBox?.width ?? 0).toBeGreaterThan(560);
  const rightOverflow = await page.locator(".ct-codex-ai").evaluate((root) => {
    const viewportRight = window.innerWidth;
    return Array.from(root.querySelectorAll("*"))
      .map((node) => {
        const rect = node.getBoundingClientRect();
        return {
          text: (node.textContent ?? "").trim().slice(0, 80),
          right: rect.right,
          width: rect.width,
        };
      })
      .filter((box) => box.width > 1 && box.right > viewportRight + 1);
  });
  expect(rightOverflow).toEqual([]);

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
  expect(exported).toContain("源码位置: lib/login/session.ts:L42-L88");
  expect(exported).toContain(
    "源码链接: /workspaces/ws-1?tab=source&sourcePath=lib%2Flogin%2Fsession.ts&line=42",
  );
  expect(exported).toContain("任务产物: run-spdk-001 · task_artifact_manifest.json");
  expect(exported).toContain(
    "产物链接: /api/workbench/task-runs/run-spdk-001/artifacts/content/task_artifact_manifest.json",
  );
  expect(exported).toContain("时间: 2026-06-28T00:00:00Z");
  expect(exported).toContain("时间: 2026-06-28T00:00:01Z");
});

test("AI conversation mobile layout keeps navigation and topbar controls within the viewport", async ({ page }) => {
  await mockReadableConversation(page, {
    assistantContent:
      "CodeTalk 已折叠一段疑似源码全文输出，避免外部 agent 把大文件直接刷进 AI 线程。\n\n" +
      "证据文件：`lib/nvmf/auth.c`、`lib/nvmf/ctrlr.c`、`lib/nvmf/ctrlr_bdev.c`、`lib/nvmf/ctrlr_discovery.c`",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/ai/conv-1", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "登录模块 AI 调查线程" })).toBeVisible();

  const metrics = await page.evaluate(() => {
    const viewportRight = window.innerWidth;
    const topbar = document.querySelector(".ct-codex-ai__topbar");
    const main = document.querySelector(".ct-codex-ai__main");
    const topbarRect = topbar?.getBoundingClientRect();
    const topbarStyle = topbar ? window.getComputedStyle(topbar) : null;
    const topbarControls = Array.from(
      document.querySelectorAll(".ct-codex-ai__topbar select, .ct-codex-ai__topbar button"),
    ).map((node) => {
      const rect = node.getBoundingClientRect();
      const parentTopbar = node.closest(".ct-codex-ai__topbar");
      const parentRect = parentTopbar?.getBoundingClientRect();
      return {
        text: (node.textContent ?? "").trim(),
        left: rect.left,
        right: rect.right,
        width: rect.width,
        parentLeft: parentRect?.left ?? 0,
        parentRight: parentRect?.right ?? 0,
      };
    });
    const rightOverflow = Array.from(document.querySelectorAll("body *"))
      .map((node) => {
        const rect = node.getBoundingClientRect();
        return {
          tag: node.nodeName,
          className: String((node as HTMLElement).className || "").slice(0, 80),
          text: (node.textContent ?? "").trim().slice(0, 80),
          left: rect.left,
          right: rect.right,
          width: rect.width,
        };
      })
      .filter((box) => box.width > 2 && box.right > viewportRight + 1);
    return {
      viewportRight,
      documentScrollWidth: document.documentElement.scrollWidth,
      mainRight: main?.getBoundingClientRect().right ?? 0,
      topbar: {
        count: document.querySelectorAll(".ct-codex-ai__topbar").length,
        left: topbarRect?.left ?? 0,
        right: topbarRect?.right ?? 0,
        width: topbarRect?.width ?? 0,
        flexDirection: topbarStyle?.flexDirection ?? "",
        flexWrap: topbarStyle?.flexWrap ?? "",
        display: topbarStyle?.display ?? "",
        alignItems: topbarStyle?.alignItems ?? "",
      },
      topbarControls,
      rightOverflow,
    };
  });

  expect(metrics.documentScrollWidth).toBeLessThanOrEqual(metrics.viewportRight);
  expect(metrics.mainRight).toBeLessThanOrEqual(metrics.viewportRight);
  expect(metrics.topbarControls.length).toBeGreaterThanOrEqual(3);
  expect(
    metrics.topbarControls.every(
      (box) => box.left >= -1 && box.right <= metrics.viewportRight + 1,
    ),
    JSON.stringify({ topbar: metrics.topbar, controls: metrics.topbarControls }, null, 2),
  ).toBe(true);
  expect(metrics.rightOverflow).toEqual([]);
});

test("AI conversation degrades unsafe source and artifact references without links", async ({ page }, testInfo) => {
  await mockReadableConversation(page, {
    extraReferences: [
      {
        source_type: "workspace_source",
        source_id: "src-unsafe",
        title: "../secrets.env",
        excerpt: "异常证据路径来自外部 agent，不能作为可打开源码链接。",
        metadata: {
          workspace_id: "ws-1",
          path: "../secrets.env",
          start_line: 1,
          end_line: 2,
        },
      },
      {
        source_type: "workbench_task_artifact",
        source_id: "run-spdk-001//etc/passwd",
        title: "/etc/passwd",
        excerpt: "异常产物路径来自外部 agent，不能作为可下载产物链接。",
        metadata: {
          workspace_id: "ws-1",
          task_run_id: "run-spdk-001",
        },
      },
    ],
  });
  await page.setViewportSize({ width: 1440, height: 920 });
  await page.goto("/ai/conv-1", { waitUntil: "domcontentloaded" });

  await page.getByText(/展开其余/).click();
  await expect(page.getByText("../secrets.env", { exact: true })).toBeVisible();
  const unsafeCard = page.locator(".ct-ai-ref", { hasText: "../secrets.env" });
  await expect(unsafeCard).toBeVisible();
  await expect(unsafeCard.getByRole("link", { name: "打开源码" })).toHaveCount(0);
  const unsafeArtifactCard = page.locator(".ct-ai-ref", { hasText: "/etc/passwd" });
  await expect(unsafeArtifactCard).toBeVisible();
  await expect(unsafeArtifactCard.getByRole("link", { name: "打开产物" })).toHaveCount(0);

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出" }).click();
  const download = await downloadPromise;
  const exportPath = testInfo.outputPath("ai-thread-unsafe-source-export.md");
  await download.saveAs(exportPath);
  const exported = fs.readFileSync(exportPath, "utf8");
  expect(exported).toContain("../secrets.env");
  expect(exported).toContain("源码位置: ../secrets.env:L1-L2");
  expect(exported).toContain("/etc/passwd");
  expect(exported).toContain("任务产物: run-spdk-001 · /etc/passwd");
  expect(exported).not.toContain("sourcePath=..%2Fsecrets.env");
  expect(exported).not.toContain("artifacts/content/%2Fetc%2Fpasswd");
  expect(exported).not.toContain("artifacts/content//etc/passwd");
});

test("AI conversation export redacts JSON and YAML style secrets", async ({ page }, testInfo) => {
  const jsonSecret = "jsonStyleSecretLeakValue1234567890";
  const yamlSecret = "yamlStyleSecretLeakValue1234567890";
  await mockReadableConversation(page, {
    assistantContent:
      `模型返回配置摘要：{"api_key":"${jsonSecret}","status":"failed"}\n` +
      `诊断提示：password: ${yamlSecret}`,
    extraReferences: [
      {
        source_type: "workspace_report",
        source_id: "report-secret-json",
        title: "密钥诊断片段",
        excerpt:
          `{"access_token": "${jsonSecret}", "note": "must be redacted"}\n` +
          `password: ${yamlSecret}`,
        metadata: { workspace_id: "ws-1" },
      },
    ],
  });
  await page.setViewportSize({ width: 1440, height: 920 });
  await page.goto("/ai/conv-1", { waitUntil: "domcontentloaded" });

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出" }).click();
  const download = await downloadPromise;
  const exportPath = testInfo.outputPath("ai-thread-json-yaml-secret-export.md");
  await download.saveAs(exportPath);
  const exported = fs.readFileSync(exportPath, "utf8");

  expect(exported).toContain("<redacted>");
  expect(exported).not.toContain(jsonSecret);
  expect(exported).not.toContain(yamlSecret);
  expect(exported).not.toMatch(/"api_key"\s*:\s*"(?!<redacted>)[^"]+"/i);
  expect(exported).not.toMatch(/password:\s*(?!<redacted>)[^\s]+/i);
});

test("AI conversation shows workspace source and material references after a real send", async ({ page }) => {
  let messagePosted = false;
  const runtimeRun = {
    id: "run-send-source",
    conversation_id: "conv-send-source",
    status: "running",
    cursor: 0,
    error: null,
    model: "test",
    token_usage: {},
    created_at: "2026-06-28T00:00:02Z",
    started_at: "2026-06-28T00:00:02Z",
    completed_at: null,
  };
  const sourceFirstRefs = [
    {
      source_type: "workspace_material",
      source_id: "mat-reconnect",
      title: "requirements.md",
      excerpt: "必须覆盖 reconnect timeout 和恢复观测点。",
      metadata: { workspace_id: "ws-send-source" },
    },
    {
      source_type: "workspace_source",
      source_id: "src-connect",
      title: "lib/nvmf/connect.c",
      excerpt: "spdk_nvmf_connect_probe validates queue setup before IO.",
      metadata: {
        workspace_id: "ws-send-source",
        path: "lib/nvmf/connect.c",
        start_line: 12,
        end_line: 64,
      },
    },
    {
      source_type: "workspace_report",
      source_id: "report-old",
      title: "旧报告",
      excerpt: "历史报告只能作为补充。",
      metadata: { workspace_id: "ws-send-source" },
    },
  ];

  await page.route("**/api/workspaces", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: [
        {
          id: "ws-send-source",
          name: "SPDK 工作区",
          repo_path: "/repo/spdk",
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
  await page.route("**/api/workspaces/ws-send-source", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        id: "ws-send-source",
        name: "SPDK 工作区",
        repo_path: "/repo/spdk",
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
    });
  });
  await page.route("**/api/workspaces/ws-send-source/versions", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: [] });
  });
  await page.route("**/api/workspaces/ws-send-source/embedding-status", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: { rag_ready: true, total_chunks: 2, active_materials: 0 },
    });
  });
  await page.route("**/api/workspaces/ws-send-source/source-file?**", async (route) => {
    const url = new URL(route.request().url());
    expect(url.searchParams.get("path")).toBe("lib/nvmf/connect.c");
    expect(url.searchParams.get("line")).toBe("12");
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        path: "lib/nvmf/connect.c",
        start_line: 12,
        end_line: 64,
        total_lines: 120,
        content: "12: spdk_nvmf_connect_probe validates queue setup before IO.\n13: return 0;",
      },
    });
  });
  await page.route("**/api/settings/agent-runtimes?enabled=true", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [] } });
  });
  await page.route("**/api/ai/conversations?workspace_id=ws-send-source&limit=50", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: { items: [] },
    });
  });
  await page.route("**/api/ai/conversations/conv-send-source", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        id: "conv-send-source",
        scope_type: "workspace",
        scope_id: "ws-send-source",
        workspace_id: "ws-send-source",
        memory_namespace: "workspace:ws-send-source",
        title: "SPDK 源码优先线程",
        status: messagePosted ? "running" : "idle",
        initial_context: {},
        created_at: "2026-06-28T00:00:00Z",
        updated_at: "2026-06-28T00:00:00Z",
        latest_run: messagePosted ? runtimeRun : null,
      },
    });
  });
  await page.route("**/api/ai/conversations/conv-send-source/messages", async (route) => {
    if (route.request().method() === "POST") {
      const body = JSON.parse(route.request().postData() ?? "{}") as { content?: string };
      expect(body.content).toContain("connect");
      messagePosted = true;
      await route.fulfill({
        headers: jsonHeaders(route.request().headers().origin),
        json: {
          message: {
            id: "msg-send-user",
            conversation_id: "conv-send-source",
            run_id: "run-send-source",
            role: "user",
            content: body.content,
            references: sourceFirstRefs,
            actions: [],
            created_at: "2026-06-28T00:00:02Z",
          },
          run: runtimeRun,
          references: sourceFirstRefs,
        },
      });
      return;
    }
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        items: messagePosted
          ? [
              {
                id: "msg-send-user",
                conversation_id: "conv-send-source",
                run_id: "run-send-source",
                role: "user",
                content: "分析 SPDK nvmf connect 的外部可观测行为",
                references: sourceFirstRefs,
                actions: [],
                created_at: "2026-06-28T00:00:02Z",
              },
            ]
          : [],
      },
    });
  });
  await page.route("**/api/ai/conversations/conv-send-source/stream?cursor=0", async (route) => {
    await route.fulfill({
      headers: {
        ...jsonHeaders(route.request().headers().origin),
        "Content-Type": "text/event-stream",
      },
      body: [
        'data: {"event_id":1,"run_id":"run-send-source","conversation_id":"conv-send-source","event_type":"status","payload":{"status":"running","message":"正在读取工作区源码、输入材料上下文。"},"created_at":"2026-06-28T00:00:03Z"}',
        "",
      ].join("\n"),
    });
  });

  await page.goto("/ai/conv-send-source", { waitUntil: "domcontentloaded" });
  await expect(page.getByText("优先召回源码、输入材料")).toBeVisible();

  const input = page.getByPlaceholder(/像 Codex 一样继续追问/);
  await input.fill("分析 SPDK nvmf connect 的外部可观测行为");
  const sendButton = page.getByRole("button", { name: "发送" });
  await sendButton.hover();
  await sendButton.click();

  await expect(page.getByText("requirements.md")).toBeVisible();
  await expect(page.getByText("lib/nvmf/connect.c", { exact: true })).toBeVisible();
  await expect(page.getByText("lib/nvmf/connect.c:L12-L64")).toBeVisible();
  await expect(page.getByText("必须覆盖 reconnect timeout")).toBeVisible();
  await expect(page.getByText("历史报告只能作为补充。")).toBeVisible();

  await page.getByRole("link", { name: "打开源码" }).click();
  await expect(page).toHaveURL(/\/workspaces\/ws-send-source\?tab=source&sourcePath=lib%2Fnvmf%2Fconnect\.c&line=12/);
  await expect(page.getByLabel("源码搜索")).toHaveValue("lib/nvmf/connect.c");
  await expect(page.locator("pre")).toContainText("spdk_nvmf_connect_probe validates queue setup");
});

test("AI conversation page skips decorative atmosphere layers for tool performance", async ({ page }) => {
  await mockReadableConversation(page);
  await page.setViewportSize({ width: 1440, height: 920 });
  await page.goto("/ai/conv-1", { waitUntil: "domcontentloaded" });

  await expect(page.getByRole("heading", { name: "登录模块 AI 调查线程" })).toBeVisible();
  await expect(page.locator(".ct-atmosphere")).toHaveCount(0);
});

test("AI home avoids staggered list animations for large thread hubs", async ({ page }) => {
  const workspaces = Array.from({ length: 24 }, (_, index) => ({
    id: `ws-${index + 1}`,
    name: `SPDK 项目 ${index + 1}`,
    repo_path: `/Volumes/Media/dpdk/spdk-${index + 1}`,
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
  }));
  const threads = Array.from({ length: 50 }, (_, index) => ({
    id: `conv-large-${index + 1}`,
    scope_type: "workspace",
    scope_id: "ws-1",
    workspace_id: "ws-1",
    memory_namespace: "workspace:ws-1",
    title: `SPDK 长线程 ${index + 1}`,
    status: index === 0 ? "running" : "idle",
    initial_context: {},
    created_at: "2026-06-28T00:00:00Z",
    updated_at: `2026-06-28T00:${String(index).padStart(2, "0")}:00Z`,
  }));

  await page.route("**/api/workspaces", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: workspaces });
  });
  await page.route("**/api/settings/agent-runtimes?enabled=true", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [] } });
  });
  await page.route("**/api/ai/conversations?limit=100", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: threads } });
  });
  await page.route("**/api/ai/conversations?limit=3", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: threads.slice(0, 3) } });
  });

  await page.setViewportSize({ width: 1440, height: 920 });
  await page.goto("/ai", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "按项目管理持续对话" })).toBeVisible();
  await expect(page.locator(".ct-thread-card")).toHaveCount(50);

  const listMotion = await page.locator(".ct-thread-project, .ct-thread-card").evaluateAll((nodes) =>
    nodes.map((node) => {
      const element = node as HTMLElement;
      const styles = window.getComputedStyle(element);
      return {
        className: element.className,
        inlineAnimationDelay: element.style.animationDelay,
        animationName: styles.animationName,
        animationDuration: styles.animationDuration,
      };
    }),
  );

  expect(
    listMotion.filter((item) => item.inlineAnimationDelay || item.animationName !== "none"),
    "large AI thread/project lists should not run staggered entry animations",
  ).toEqual([]);
});

test("AI mini dock keeps idle background polling quiet on non-AI pages", async ({ page }) => {
  let dockListRequests = 0;
  await page.route("**/api/workspaces", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: [] });
  });
  await page.route("**/api/ai/conversations?limit=3", async (route) => {
    dockListRequests += 1;
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        items: [
          {
            id: "idle-dock-thread",
            scope_type: "global",
            scope_id: "global",
            workspace_id: null,
            memory_namespace: "global",
            title: "空闲线程",
            status: "idle",
            initial_context: {},
            created_at: "2026-06-28T00:00:00Z",
            updated_at: "2026-06-28T00:00:00Z",
          },
        ],
      },
    });
  });

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("link", { name: /空闲线程/ })).toBeVisible();
  await expect.poll(() => dockListRequests).toBeGreaterThanOrEqual(1);
  await page.waitForTimeout(9500);

  expect(dockListRequests).toBeLessThanOrEqual(2);
});

test("AI mini dock keeps the last known thread when a background refresh fails", async ({ page }) => {
  let dockListRequests = 0;
  await page.route("**/api/workspaces", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: [] });
  });
  await page.route("**/api/ai/conversations?limit=3", async (route) => {
    dockListRequests += 1;
    if (dockListRequests > 1) {
      await route.fulfill({
        headers: jsonHeaders(route.request().headers().origin),
        status: 500,
        json: { detail: "temporary backend restart" },
      });
      return;
    }
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        items: [
          {
            id: "running-dock-thread",
            scope_type: "workspace",
            scope_id: "ws-spdk",
            workspace_id: "ws-spdk",
            memory_namespace: "workspace:ws-spdk",
            title: "SPDK 生成中线程",
            status: "running",
            initial_context: {},
            created_at: "2026-06-28T00:00:00Z",
            updated_at: "2026-06-28T00:00:00Z",
          },
        ],
      },
    });
  });

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("link", { name: /SPDK 生成中线程/ })).toBeVisible();
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect.poll(() => dockListRequests).toBe(2);
  const dock = page.locator(".ct-ai-dock");
  await expect(dock).toContainText("SPDK 生成中线程");
  await expect(dock).not.toHaveText(/^AI 线程$/);
});

test("AI mini dock does not add body-wide mutation observers on non-AI pages", async ({ page }) => {
  await page.addInitScript(() => {
    const NativeMutationObserver = window.MutationObserver;
    let bodySubtreeObserveCount = 0;
    class CountingMutationObserver extends NativeMutationObserver {
      constructor(callback: MutationCallback) {
        super(callback);
      }

      observe(target: Node, options?: MutationObserverInit) {
        if (target === document.body && options?.subtree) {
          bodySubtreeObserveCount += 1;
        }
        return super.observe(target, options);
      }
    }
    Object.defineProperty(window, "MutationObserver", {
      configurable: true,
      writable: true,
      value: CountingMutationObserver,
    });
    Object.defineProperty(window, "__codetalkBodySubtreeObserverCount", {
      configurable: true,
      get: () => bodySubtreeObserveCount,
    });
  });
  await page.route("**/api/workspaces", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: [] });
  });
  await page.route("**/api/ai/conversations?limit=3", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        items: [
          {
            id: "idle-dock-thread",
            scope_type: "global",
            scope_id: "global",
            workspace_id: null,
            memory_namespace: "global",
            title: "空闲线程",
            status: "idle",
            initial_context: {},
            created_at: "2026-06-28T00:00:00Z",
            updated_at: "2026-06-28T00:00:00Z",
          },
        ],
      },
    });
  });

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("link", { name: /空闲线程/ })).toBeVisible();

  const observerCount = await page.evaluate(() => {
    const value = (window as Window & { __codetalkBodySubtreeObserverCount?: number })
      .__codetalkBodySubtreeObserverCount;
    return typeof value === "number" ? value : -1;
  });
  expect(observerCount).toBeLessThanOrEqual(1);
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
    tabIndex: (element as HTMLElement).tabIndex,
  }));
  expect(metrics.readerScrollHeight).toBeGreaterThan(metrics.readerClientHeight + 300);
  expect(metrics.documentScrollHeight).toBeLessThanOrEqual(metrics.viewportHeight + 24);
  expect(metrics.scrollBehavior).not.toBe("smooth");
  expect(metrics.overscrollBehavior).toBe("contain");
  expect(metrics.tabIndex).toBe(0);

  const readerBox = await page.getByLabel("AI 线程对话内容").boundingBox();
  expect(readerBox).not.toBeNull();
  await page.mouse.move(readerBox!.x + readerBox!.width / 2, readerBox!.y + readerBox!.height / 2);
  await page.mouse.wheel(0, 800);
  await expect
    .poll(() => page.getByLabel("AI 线程对话内容").evaluate((element) => element.scrollTop))
    .toBeGreaterThan(100);
});

test("AI conversation avoids per-message entry animations in long histories", async ({ page }) => {
  const extraMessages = Array.from({ length: 78 }, (_, index) => ({
    id: `msg-long-${index}`,
    conversation_id: "conv-1",
    run_id: `run-long-${index}`,
    role: index % 2 === 0 ? "user" : "assistant",
    content: `长历史消息 ${index + 1}：SPDK NVMe-oF connect、reconnect、timeout、黑盒观测点。`,
    references: [],
    actions: [],
    created_at: `2026-06-28T00:${String(index).padStart(2, "0")}:00Z`,
  }));
  await mockReadableConversation(page, { extraMessages });
  await page.setViewportSize({ width: 1440, height: 760 });
  await page.goto("/ai/conv-1", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".ct-codex-message")).toHaveCount(80);

  const messageMotion = await page.locator(".ct-codex-message").evaluateAll((nodes) =>
    nodes.map((node) => {
      const styles = window.getComputedStyle(node as HTMLElement);
      return {
        animationName: styles.animationName,
        animationDuration: styles.animationDuration,
      };
    }),
  );
  expect(
    messageMotion.filter((item) => item.animationName !== "none"),
    "long AI histories should not animate every message on render",
  ).toEqual([]);
});

test("AI conversation preserves the reader position when the user scrolls up during streaming", async ({
  page,
}) => {
  let releaseSecondChunk = () => {};
  let releaseThirdChunk = () => {};
  let streamRequestedResolve: (() => void) | null = null;
  const streamRequested = new Promise<void>((resolve) => {
    streamRequestedResolve = resolve;
  });
  const secondChunkGate = new Promise<void>((resolve) => {
    releaseSecondChunk = resolve;
  });
  const thirdChunkGate = new Promise<void>((resolve) => {
    releaseThirdChunk = resolve;
  });
  const server = http.createServer(async (_req, res) => {
    streamRequestedResolve?.();
    res.writeHead(200, {
      "Access-Control-Allow-Origin": frontendOrigin,
      "Access-Control-Allow-Credentials": "true",
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    });
    res.write(
      [
        'data: {"event_id":1,"run_id":"run-scroll","conversation_id":"conv-scroll","event_type":"delta","payload":{"content":"第一段流式回答。\\n\\n"},"created_at":"2026-06-28T00:00:02Z"}',
        "",
        "",
      ].join("\n"),
    );
    await secondChunkGate;
    res.write(
      [
        'data: {"event_id":2,"run_id":"run-scroll","conversation_id":"conv-scroll","event_type":"delta","payload":{"content":"第二段流式回答到达时，用户仍应停留在历史阅读位置。\\n\\n"},"created_at":"2026-06-28T00:00:03Z"}',
        "",
        "",
      ].join("\n"),
    );
    await thirdChunkGate;
    res.write(
      [
        'data: {"event_id":3,"run_id":"run-scroll","conversation_id":"conv-scroll","event_type":"delta","payload":{"content":"第三段流式回答到达时，点击跳转后的阅读器应继续跟随最新内容。\\n\\n"},"created_at":"2026-06-28T00:00:04Z"}',
        "",
        "",
      ].join("\n"),
    );
    await new Promise((resolve) => setTimeout(resolve, 2000));
    res.end();
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as AddressInfo).port;
  test.info().attach("stream-server-port", {
    body: String(port),
    contentType: "text/plain",
  });

  try {
    const longAssistant = Array.from({ length: 24 }, (_, index) =>
      `历史答案 ${index + 1}：登录失败、权限失效、弱网重试、审计日志验证和恢复路径。`,
    ).join("\n\n");

    await page.route("**/api/workspaces", async (route) => {
      await route.fulfill({
        headers: jsonHeaders(route.request().headers().origin),
        json: [
          {
            id: "ws-scroll",
            name: "滚动项目",
            repo_path: "/repo/scroll",
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
    await page.route("**/api/ai/conversations?workspace_id=ws-scroll&limit=50", async (route) => {
      await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [] } });
    });
    await page.route("**/api/ai/conversations/conv-scroll", async (route) => {
      await route.fulfill({
        headers: jsonHeaders(route.request().headers().origin),
        json: {
          id: "conv-scroll",
          scope_type: "workspace",
          scope_id: "ws-scroll",
          workspace_id: "ws-scroll",
          memory_namespace: "workspace:ws-scroll",
          title: "流式滚动线程",
          status: "running",
          initial_context: {},
          created_at: "2026-06-28T00:00:00Z",
          updated_at: "2026-06-28T00:00:00Z",
          latest_run: {
            id: "run-scroll",
            conversation_id: "conv-scroll",
            status: "running",
            cursor: 0,
            error: null,
            model: "test",
            token_usage: {},
            created_at: "2026-06-28T00:00:01Z",
            started_at: "2026-06-28T00:00:01Z",
            completed_at: null,
          },
        },
      });
    });
    await page.route("**/api/ai/conversations/conv-scroll/messages", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({
        headers: jsonHeaders(route.request().headers().origin),
        json: {
          items: [
            {
              id: "msg-scroll-user",
              conversation_id: "conv-scroll",
              run_id: "run-history",
              role: "user",
              content: "先生成很长的历史回答",
              references: [],
              actions: [],
              created_at: "2026-06-28T00:00:00Z",
            },
            {
              id: "msg-scroll-assistant",
              conversation_id: "conv-scroll",
              run_id: "run-history",
              role: "assistant",
              content: longAssistant,
              references: [],
              actions: [],
              created_at: "2026-06-28T00:00:01Z",
            },
          ],
        },
      });
    });
    await page.route("**/api/ai/conversations/conv-scroll/stream?cursor=0", async (route) => {
      await route.continue({ url: `http://127.0.0.1:${port}/stream` });
    });

    await page.setViewportSize({ width: 1440, height: 760 });
    await page.goto("/ai/conv-scroll", { waitUntil: "domcontentloaded" });
    await streamRequested;
    await expect(page.getByText("第一段流式回答。")).toBeVisible();

    const reader = page.getByLabel("AI 线程对话内容");
    await expect
      .poll(() =>
        reader.evaluate((element) => element.scrollHeight - element.clientHeight - element.scrollTop),
      )
      .toBeLessThan(120);

    await reader.focus();
    await expect
      .poll(() => page.evaluate(() => document.activeElement?.getAttribute("aria-label")))
      .toBe("AI 线程对话内容");
    const beforeKeyboardScroll = await reader.evaluate((element) => element.scrollTop);
    await page.keyboard.press("PageUp");
    await expect
      .poll(() => reader.evaluate((element) => element.scrollTop))
      .toBeLessThan(beforeKeyboardScroll - 80);
    await expect(page.getByRole("button", { name: "跳到最新回复" })).toBeVisible();
    await page.getByRole("button", { name: "跳到最新回复" }).click();
    await expect
      .poll(() =>
        reader.evaluate((element) => element.scrollHeight - element.clientHeight - element.scrollTop),
      )
      .toBeLessThan(120);

    const readerBox = await reader.boundingBox();
    expect(readerBox).not.toBeNull();
    await page.mouse.move(readerBox!.x + readerBox!.width / 2, readerBox!.y + readerBox!.height / 2);
    const beforeUserScroll = await reader.evaluate((element) => element.scrollTop);
    await page.mouse.wheel(0, -900);
    await expect
      .poll(() => reader.evaluate((element) => element.scrollTop))
      .toBeLessThan(beforeUserScroll - 100);
    await page.waitForTimeout(50);
    const userScrollTop = await reader.evaluate((element) => element.scrollTop);
    await expect(page.getByRole("button", { name: "跳到最新回复" })).toBeVisible();

    releaseSecondChunk();
    await expect(page.getByText("第二段流式回答到达时")).toHaveCount(1);
    await page.waitForTimeout(100);

    const afterSecondChunk = await reader.evaluate((element) => ({
      scrollTop: element.scrollTop,
      distanceFromBottom: element.scrollHeight - element.clientHeight - element.scrollTop,
    }));
    expect(Math.abs(afterSecondChunk.scrollTop - userScrollTop)).toBeLessThan(80);
    expect(afterSecondChunk.distanceFromBottom).toBeGreaterThan(180);
    await expect(page.getByRole("button", { name: "跳到最新回复" })).toBeVisible();

    await page.getByRole("button", { name: "跳到最新回复" }).click();
    await expect
      .poll(() =>
        reader.evaluate((element) => element.scrollHeight - element.clientHeight - element.scrollTop),
      )
      .toBeLessThan(120);
    releaseThirdChunk();
    await expect(page.getByText("第三段流式回答到达时")).toHaveCount(1);
    await expect
      .poll(() =>
        reader.evaluate((element) => element.scrollHeight - element.clientHeight - element.scrollTop),
      )
      .toBeLessThan(120);
    await expect(page.getByRole("button", { name: "跳到最新回复" })).toBeHidden();
  } finally {
    (
      server as http.Server & {
        closeAllConnections?: () => void;
        closeIdleConnections?: () => void;
      }
    ).closeAllConnections?.();
    (
      server as http.Server & {
        closeAllConnections?: () => void;
        closeIdleConnections?: () => void;
      }
    ).closeIdleConnections?.();
    await new Promise<void>((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
  }
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

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出" }).click();
  const download = await downloadPromise;
  const exportPath = test.info().outputPath("ai-thread-diagnostic-export.md");
  await download.saveAs(exportPath);
  const exported = fs.readFileSync(exportPath, "utf8");
  expect(exported).toContain("最终答案：覆盖 reconnect timeout 的黑盒观察点。");
  expect(exported).not.toContain("正在准备工作区源码上下文");
  expect(exported).not.toContain("正在读取 lib/nvmf/connect.c");
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
      messageLineHeight: Number.parseFloat(messageStyles.lineHeight),
      messageRadius: Number.parseFloat(messageStyles.borderTopLeftRadius),
      textareaFontSize: Number.parseFloat(textareaStyles.fontSize),
      composerWidth: composer.width,
      appWidth: app.width,
    };
  });

  expect(layout.overflows).toEqual([]);
  expect(layout.overlaps).toEqual([]);
  expect(layout.messageFontSize).toBeGreaterThanOrEqual(14);
  expect(layout.messageFontSize).toBeLessThanOrEqual(16);
  expect(layout.messageLineHeight).toBeLessThanOrEqual(22);
  expect(layout.messageRadius).toBeLessThanOrEqual(10);
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
