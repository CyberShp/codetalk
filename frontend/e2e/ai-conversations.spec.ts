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
    userReferences?: Array<Record<string, unknown>>;
    references?: Array<Record<string, unknown>>;
    extraReferences?: Array<Record<string, unknown>>;
    extraMessages?: Array<Record<string, unknown>>;
  } = {},
) {
  const assistantContent =
    options.assistantContent ?? "建议补充登录失败、权限失效、弱网重试和审计日志验证。";
  const references = options.references ?? [
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
            references: options.userReferences ?? [],
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

test("AI conversation evidence cards prioritize latest assistant precise source refs", async ({ page }) => {
  await mockReadableConversation(page, {
    userReferences: [
      {
        source_type: "workspace_source",
        source_id: "ws-1:lib/iscsi/iscsi.c:1-41",
        title: "lib/iscsi/iscsi.c:1",
        excerpt: "1: /* SPDX-License-Identifier */",
        metadata: {
          workspace_id: "ws-1",
          path: "lib/iscsi/iscsi.c",
          start_line: 1,
          end_line: 41,
        },
      },
      {
        source_type: "workspace_source",
        source_id: "ws-1:lib/iscsi/conn.c:1-41",
        title: "lib/iscsi/conn.c:1",
        excerpt: "1: /* SPDX-License-Identifier */",
        metadata: {
          workspace_id: "ws-1",
          path: "lib/iscsi/conn.c",
          start_line: 1,
          end_line: 41,
        },
      },
    ],
    references: [
      {
        source_type: "workspace_source",
        source_id: "ws-1:lib/iscsi/iscsi.c:782-834",
        title: "lib/iscsi/iscsi.c:794",
        excerpt: "794: SPDK_ERRLOG(\"unsupported AuthMethod %.64s\\n\", method);",
        metadata: {
          workspace_id: "ws-1",
          path: "lib/iscsi/iscsi.c",
          start_line: 782,
          end_line: 834,
        },
      },
      {
        source_type: "workspace_source",
        source_id: "ws-1:lib/iscsi/conn.c:180-232",
        title: "lib/iscsi/conn.c:192",
        excerpt: "192: conn->disable_chap = portal->group->disable_chap;",
        metadata: {
          workspace_id: "ws-1",
          path: "lib/iscsi/conn.c",
          start_line: 180,
          end_line: 232,
        },
      },
    ],
  });
  await page.goto("/ai/conv-1", { waitUntil: "domcontentloaded" });

  const cards = page.locator(".ct-ai-ref");
  await expect(cards.first()).toContainText("lib/iscsi/iscsi.c:794");
  await expect(cards.first()).toContainText("lib/iscsi/iscsi.c:L782-L834");
  await expect(cards.nth(1)).toContainText("lib/iscsi/conn.c:192");
  await expect(cards.nth(1)).toContainText("lib/iscsi/conn.c:L180-L232");
});

test("AI test activity task card explains contract and opens workflow with parameters", async ({
  page,
}) => {
  const target = "针对 iSCSI login 输出 SFMEA 和黑盒测试用例";
  const outputs = "sfmea.json,black_box_cases.json";
  await mockReadableConversation(page, {
    extraMessages: [
      {
        id: "msg-test-activity-user",
        conversation_id: "conv-1",
        run_id: "run-test-activity",
        role: "user",
        content: target,
        references: [],
        actions: [],
        created_at: "2026-06-28T00:00:02Z",
      },
      {
        id: "msg-test-activity-assistant",
        conversation_id: "conv-1",
        run_id: "run-test-activity",
        role: "assistant",
        content: "已生成测试活动契约，可切换到工作流运行并产出文件。",
        references: [],
        actions: [
          {
            id: "test_activity_task_card",
            kind: "test_activity",
            label: "测试活动任务卡",
            target,
            domain_profiles: ["iscsi_login", "resource_lifecycle"],
            recommended_outputs: ["sfmea.json", "black_box_cases.json"],
            evidence_policy: {
              source_first: true,
              required_sources: ["workspace_source", "gitnexus", "cgc"],
              minimum_evidence: 3,
            },
            focus_rationale: [
              "用户显式要求 iSCSI login、SFMEA、黑盒用例",
              "领域画像 iscsi_login 要覆盖 CHAP、digest、session reset",
              "项目画像建议优先读取 lib/iscsi 与 test/iscsi_tgt",
            ],
            workflow_template_id: "source_flow_sfmea_blackbox",
            workspace_id: "ws-1",
            href: `/workbench?workflow=source_flow_sfmea_blackbox&workspace_id=ws-1&target=${encodeURIComponent(
              target,
            )}&outputs=${encodeURIComponent(outputs)}`,
            edit_contract_href: "/workbench/designer",
          },
        ],
        created_at: "2026-06-28T00:00:03Z",
      },
    ],
  });

  await page.goto("/ai/conv-1", { waitUntil: "domcontentloaded" });

  const taskCard = page.locator(".ct-test-activity-card").filter({ hasText: "测试活动任务卡" });
  await expect(taskCard).toBeVisible();
  await expect(taskCard).toContainText(target);
  await expect(taskCard.getByLabel("识别到的测试画像")).toContainText("iscsi_login");
  await expect(taskCard.getByText("推荐交付件")).toBeVisible();
  await expect(taskCard.getByText("sfmea.json · black_box_cases.json")).toBeVisible();
  await expect(taskCard.getByText("证据策略")).toBeVisible();
  await expect(taskCard.getByText("先查工作区源码、GitNexus、CGC")).toBeVisible();
  await expect(taskCard.getByText("至少 3 条证据")).toBeVisible();
  await taskCard.getByText("为什么这样定测试方向").click();
  await expect(taskCard.getByText("领域画像 iscsi_login 要覆盖 CHAP")).toBeVisible();

  await taskCard.getByRole("link", { name: /启动工作流/ }).click();
  await expect(page).toHaveURL(/\/workbench\?/);
  const url = new URL(page.url());
  expect(url.searchParams.get("workflow")).toBe("source_flow_sfmea_blackbox");
  expect(url.searchParams.get("workspace_id")).toBe("ws-1");
  expect(url.searchParams.get("target")).toBe(target);
  expect(url.searchParams.get("outputs")).toBe(outputs);
});

test("AI conversation evidence cards keep long source excerpts folded", async ({ page }) => {
  const longSourceExcerpt = Array.from({ length: 26 }, (_, index) =>
    `${index + 1}: if (login_phase_${index} && chap_state_${index}) { return iscsi_login_error_${index}; }`,
  ).join("\n");
  await mockReadableConversation(page, {
    references: [
      {
        source_type: "workspace_source",
        source_id: "src-long",
        title: "lib/iscsi/iscsi.c:1262",
        excerpt: longSourceExcerpt,
        metadata: {
          workspace_id: "ws-1",
          path: "lib/iscsi/iscsi.c",
          start_line: 1262,
          end_line: 1443,
        },
      },
    ],
  });
  await page.setViewportSize({ width: 1440, height: 920 });
  await page.goto("/ai/conv-1", { waitUntil: "domcontentloaded" });

  const card = page.locator(".ct-ai-ref").first();
  await expect(card).toContainText("lib/iscsi/iscsi.c:L1262-L1443");
  await expect(card.getByText("展开证据片段")).toBeVisible();
  await expect(card.getByText("iscsi_login_error_25")).toBeHidden();

  const compactHeight = await card.evaluate((element) => element.getBoundingClientRect().height);
  expect(compactHeight).toBeLessThan(220);

  await card.getByText("展开证据片段").click();
  await expect(card.getByText("iscsi_login_error_25")).toBeVisible();
});

test("AI conversation rail filters dense projects and thread histories", async ({ page }) => {
  const workspaces = Array.from({ length: 36 }, (_, index) => ({
    id: index === 0 ? "ws-1" : `ws-${index + 1}`,
    name: index === 29 ? "SPDK production target" : `Large project ${index + 1}`,
    repo_path: `/repo/project-${index + 1}`,
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
  const threads = Array.from({ length: 42 }, (_, index) => ({
    id: index === 0 ? "conv-dense" : `conv-dense-${index + 1}`,
    scope_type: "workspace",
    scope_id: "ws-1",
    workspace_id: "ws-1",
    memory_namespace: "workspace:ws-1",
    title: index === 31 ? "rare-thread iSCSI login SFMEA" : `Dense thread ${index + 1}`,
    status: "idle",
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
  await page.route("**/api/ai/conversations?workspace_id=ws-1&limit=50", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: threads } });
  });
  await page.route("**/api/ai/conversations/conv-dense", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        ...threads[0],
        latest_run: null,
      },
    });
  });
  await page.route("**/api/ai/conversations/conv-dense/messages", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: { items: [] },
    });
  });

  await page.setViewportSize({ width: 1440, height: 920 });
  await page.goto("/ai/conv-dense", { waitUntil: "domcontentloaded" });

  await expect(page.getByText("已收起 12 个项目")).toBeVisible();
  await expect(page.getByText("已收起 18 条线程")).toBeVisible();
  await expect(page.getByText("SPDK production target")).toBeHidden();

  const denseLayout = await page.locator(".ct-codex-ai").evaluate((element) => {
    const projectList = element.querySelector(".ct-codex-ai__project-list") as HTMLElement | null;
    const newButton = element.querySelector(".ct-codex-ai__new") as HTMLElement | null;
    const projectListRect = projectList?.getBoundingClientRect();
    const newButtonRect = newButton?.getBoundingClientRect();
    return {
      projectListBottom: projectListRect?.bottom ?? 0,
      newButtonTop: newButtonRect?.top ?? 0,
      projectListClientHeight: projectList?.clientHeight ?? 0,
      projectListScrollHeight: projectList?.scrollHeight ?? 0,
    };
  });
  expect(denseLayout.projectListScrollHeight).toBeGreaterThan(denseLayout.projectListClientHeight + 80);
  expect(denseLayout.projectListBottom).toBeLessThanOrEqual(denseLayout.newButtonTop - 8);

  await page.getByLabel("搜索 AI 项目").fill("production");
  await expect(page.getByText("SPDK production target")).toBeVisible();
  await expect(page.getByText("Large project 2")).toBeHidden();

  await page.getByLabel("搜索 AI 线程").fill("rare-thread");
  await expect(page.getByText("rare-thread iSCSI login SFMEA")).toBeVisible();
  await expect(page.getByText("Dense thread 2")).toBeHidden();

  const layout = await page.locator(".ct-codex-ai").evaluate((element) => {
    const projectList = element.querySelector(".ct-codex-ai__project-list") as HTMLElement | null;
    const threadList = element.querySelector(".ct-codex-ai__thread-list") as HTMLElement | null;
    return {
      documentScrollHeight: document.documentElement.scrollHeight,
      viewportHeight: window.innerHeight,
      projectOverflowY: projectList ? window.getComputedStyle(projectList).overflowY : "",
      threadOverflowY: threadList ? window.getComputedStyle(threadList).overflowY : "",
    };
  });
  expect(layout.documentScrollHeight).toBeLessThanOrEqual(layout.viewportHeight + 24);
  expect(layout.projectOverflowY).toBe("auto");
  expect(layout.threadOverflowY).toBe("auto");
});

test("AI conversation home keeps dense project and thread histories in bounded panes", async ({ page }) => {
  const workspaces = Array.from({ length: 12 }, (_, index) => ({
    id: index === 0 ? "ws-1" : `home-ws-${index + 1}`,
    name: index === 0 ? "SPDK" : `Home project ${index + 1}`,
    repo_path: `/repo/home-project-${index + 1}`,
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
  const threads = Array.from({ length: 80 }, (_, index) => ({
    id: `home-conv-${index + 1}`,
    scope_type: "workspace",
    scope_id: "ws-1",
    workspace_id: "ws-1",
    memory_namespace: "workspace:ws-1",
    title: `SPDK long AI thread ${index + 1}`,
    status: "idle",
    latest_run: null,
    initial_context: {},
    created_at: "2026-06-28T00:00:00Z",
    updated_at: `2026-06-28T00:${String(index % 60).padStart(2, "0")}:00Z`,
  }));

  await page.route("**/api/workspaces", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: workspaces });
  });
  await page.route("**/api/ai/conversations?limit=100", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: threads } });
  });
  await page.route("**/api/settings/agent-runtimes?enabled=true", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [] } });
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/ai", { waitUntil: "domcontentloaded" });

  await expect(page.getByRole("heading", { name: "SPDK long AI thread 1", exact: true })).toBeVisible();
  const layout = await page.locator(".ct-ai-home").evaluate((element) => {
    const root = element as HTMLElement;
    const grid = root.querySelector(".ct-ai-home__grid") as HTMLElement | null;
    const projectList = root.querySelector(".ct-ai-home__project-list") as HTMLElement | null;
    const threadPane = root.querySelector(".ct-ai-home__threads") as HTMLElement | null;
    const timeline = root.querySelector(".ct-thread-timeline") as HTMLElement | null;
    const rootRect = root.getBoundingClientRect();
    const paneRect = threadPane?.getBoundingClientRect();
    const timelineRect = timeline?.getBoundingClientRect();
    return {
      documentScrollHeight: document.documentElement.scrollHeight,
      viewportHeight: window.innerHeight,
      rootBottom: rootRect.bottom,
      paneBottom: paneRect?.bottom ?? 0,
      timelineBottom: timelineRect?.bottom ?? 0,
      gridOverflowY: grid ? window.getComputedStyle(grid).overflowY : "",
      threadPaneOverflowY: threadPane ? window.getComputedStyle(threadPane).overflowY : "",
      timelineOverflowY: timeline ? window.getComputedStyle(timeline).overflowY : "",
      timelineClientHeight: timeline?.clientHeight ?? 0,
      timelineScrollHeight: timeline?.scrollHeight ?? 0,
      projectOverflowY: projectList ? window.getComputedStyle(projectList).overflowY : "",
    };
  });

  expect(layout.documentScrollHeight).toBeLessThanOrEqual(layout.viewportHeight + 24);
  expect(layout.rootBottom).toBeLessThanOrEqual(layout.viewportHeight + 1);
  expect(layout.paneBottom).toBeLessThanOrEqual(layout.viewportHeight + 1);
  expect(layout.timelineBottom).toBeLessThanOrEqual(layout.viewportHeight + 1);
  expect(layout.gridOverflowY).toBe("hidden");
  expect(layout.threadPaneOverflowY).toBe("hidden");
  expect(layout.timelineOverflowY).toBe("auto");
  expect(layout.projectOverflowY).toBe("auto");
  expect(layout.timelineScrollHeight).toBeGreaterThan(layout.timelineClientHeight + 160);
});

test("AI conversation mobile rail keeps dense project and thread lists contained", async ({ page }) => {
  const workspaces = Array.from({ length: 36 }, (_, index) => ({
    id: index === 0 ? "ws-1" : `ws-mobile-${index + 1}`,
    name: index === 29 ? "SPDK mobile production target" : `Mobile project ${index + 1}`,
    repo_path: `/repo/mobile-project-${index + 1}`,
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
  const threads = Array.from({ length: 42 }, (_, index) => ({
    id: index === 0 ? "conv-mobile-dense" : `conv-mobile-dense-${index + 1}`,
    scope_type: "workspace",
    scope_id: "ws-1",
    workspace_id: "ws-1",
    memory_namespace: "workspace:ws-1",
    title: index === 31 ? "rare mobile iSCSI login SFMEA" : `Mobile dense thread ${index + 1}`,
    status: "idle",
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
  await page.route("**/api/ai/conversations?workspace_id=ws-1&limit=50", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: threads } });
  });
  await page.route("**/api/ai/conversations/conv-mobile-dense", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        ...threads[0],
        latest_run: null,
      },
    });
  });
  await page.route("**/api/ai/conversations/conv-mobile-dense/messages", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: { items: [] },
    });
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/ai/conv-mobile-dense", { waitUntil: "domcontentloaded" });

  await expect(page.getByText("已收起 34 条线程")).toBeVisible();

  const layout = await page.locator(".ct-codex-ai").evaluate((element) => {
    const rail = element.querySelector(".ct-codex-ai__rail") as HTMLElement | null;
    const projectList = element.querySelector(".ct-codex-ai__project-list") as HTMLElement | null;
    const threadList = element.querySelector(".ct-codex-ai__thread-list") as HTMLElement | null;
    return {
      documentScrollHeight: document.documentElement.scrollHeight,
      viewportHeight: window.innerHeight,
      railHeight: rail?.getBoundingClientRect().height ?? 0,
      railClientHeight: rail?.clientHeight ?? 0,
      railScrollHeight: rail?.scrollHeight ?? 0,
      projectListRendered: (projectList?.getBoundingClientRect().height ?? 0) > 1,
      projectClientHeight: projectList?.clientHeight ?? 0,
      projectScrollHeight: projectList?.scrollHeight ?? 0,
      threadClientHeight: threadList?.clientHeight ?? 0,
      threadScrollHeight: threadList?.scrollHeight ?? 0,
      projectOverflowY: projectList ? window.getComputedStyle(projectList).overflowY : "",
      threadOverflowY: threadList ? window.getComputedStyle(threadList).overflowY : "",
    };
  });

  expect(layout.railHeight).toBeLessThanOrEqual(layout.viewportHeight * 0.72);
  expect(layout.railScrollHeight).toBeLessThanOrEqual(layout.railClientHeight + 4);
  expect(layout.documentScrollHeight).toBeLessThanOrEqual(layout.viewportHeight * 1.75);
  expect(layout.projectListRendered).toBe(false);
  expect(layout.threadClientHeight).toBeGreaterThanOrEqual(44);
  expect(layout.threadScrollHeight).toBeGreaterThan(layout.threadClientHeight + 80);
  expect(layout.threadOverflowY).toBe("auto");

  await page.getByLabel("搜索 AI 线程").fill("rare mobile");
  await expect(page.getByText("rare mobile iSCSI login SFMEA")).toBeVisible();
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
    const reader = document.querySelector(".ct-codex-ai__reader") as HTMLElement | null;
    const composer = document.querySelector(".ct-codex-composer") as HTMLElement | null;
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
      readerClientHeight: reader?.clientHeight ?? 0,
      readerScrollHeight: reader?.scrollHeight ?? 0,
      composerHeight: composer?.getBoundingClientRect().height ?? 0,
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
  expect(metrics.readerClientHeight).toBeGreaterThanOrEqual(180);
  expect(metrics.readerScrollHeight).toBeGreaterThanOrEqual(metrics.readerClientHeight);
  expect(metrics.composerHeight).toBeLessThanOrEqual(180);
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

  const containment = await page.evaluate(() => {
    const projectList = document.querySelector(".ct-ai-home__project-list") as HTMLElement | null;
    const threadTimeline = document.querySelector(".ct-thread-timeline") as HTMLElement | null;
    const rightOverflow = Array.from(document.querySelectorAll("body *"))
      .map((node) => {
        const rect = node.getBoundingClientRect();
        return {
          className: String((node as HTMLElement).className || "").slice(0, 80),
          text: (node.textContent ?? "").trim().slice(0, 80),
          right: rect.right,
          width: rect.width,
        };
      })
      .filter((box) => box.width > 2 && box.right > window.innerWidth + 1);
    return {
      documentScrollHeight: document.documentElement.scrollHeight,
      documentScrollWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      projectClientHeight: projectList?.clientHeight ?? 0,
      projectScrollHeight: projectList?.scrollHeight ?? 0,
      threadClientHeight: threadTimeline?.clientHeight ?? 0,
      threadScrollHeight: threadTimeline?.scrollHeight ?? 0,
      projectOverflowY: projectList ? window.getComputedStyle(projectList).overflowY : "",
      threadOverflowY: threadTimeline ? window.getComputedStyle(threadTimeline).overflowY : "",
      rightOverflow,
    };
  });

  expect(containment.documentScrollWidth).toBeLessThanOrEqual(containment.viewportWidth);
  expect(containment.rightOverflow).toEqual([]);
  expect(containment.documentScrollHeight).toBeLessThanOrEqual(containment.viewportHeight + 120);
  expect(containment.projectScrollHeight).toBeGreaterThan(containment.projectClientHeight + 120);
  expect(containment.threadScrollHeight).toBeGreaterThan(containment.threadClientHeight + 120);
  expect(containment.projectOverflowY).toBe("auto");
  expect(containment.threadOverflowY).toBe("auto");

  const projectList = page.locator(".ct-ai-home__project-list");
  await projectList.hover();
  await page.mouse.wheel(0, 900);
  await expect.poll(() => projectList.evaluate((element) => element.scrollTop)).toBeGreaterThan(80);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeLessThan(5);

  const threadTimeline = page.locator(".ct-thread-timeline");
  await threadTimeline.hover();
  await page.mouse.wheel(0, 1200);
  await expect.poll(() => threadTimeline.evaluate((element) => element.scrollTop)).toBeGreaterThan(120);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeLessThan(5);

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

test("AI home mobile contains dense project and thread lists inside scroll panes", async ({ page }) => {
  const workspaces = Array.from({ length: 24 }, (_, index) => ({
    id: `ws-mobile-home-${index + 1}`,
    name: `SPDK 移动项目 ${index + 1} with persistent thread history and long workspace label`,
    repo_path: `/Volumes/Media/dpdk/spdk-mobile-${index + 1}`,
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
    id: `conv-mobile-home-${index + 1}`,
    scope_type: "workspace",
    scope_id: "ws-mobile-home-1",
    workspace_id: "ws-mobile-home-1",
    memory_namespace: "workspace:ws-mobile-home-1",
    title: `SPDK 移动长线程 ${index + 1}`,
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

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/ai", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "按项目管理持续对话" })).toBeVisible();
  await expect(page.locator(".ct-thread-card")).toHaveCount(50);

  const containment = await page.evaluate(() => {
    const home = document.querySelector(".ct-ai-home") as HTMLElement | null;
    const projectList = document.querySelector(".ct-ai-home__project-list") as HTMLElement | null;
    const threadTimeline = document.querySelector(".ct-thread-timeline") as HTMLElement | null;
    const rightOverflow = Array.from(document.querySelectorAll("body *"))
      .map((node) => {
        const rect = node.getBoundingClientRect();
        return {
          className: String((node as HTMLElement).className || "").slice(0, 80),
          text: (node.textContent ?? "").trim().slice(0, 80),
          right: rect.right,
          width: rect.width,
        };
      })
      .filter((box) => box.width > 2 && box.right > window.innerWidth + 1);
    return {
      documentScrollHeight: document.documentElement.scrollHeight,
      documentScrollWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      homeHeight: home?.getBoundingClientRect().height ?? 0,
      projectClientHeight: projectList?.clientHeight ?? 0,
      projectScrollHeight: projectList?.scrollHeight ?? 0,
      threadClientHeight: threadTimeline?.clientHeight ?? 0,
      threadScrollHeight: threadTimeline?.scrollHeight ?? 0,
      projectOverflowY: projectList ? window.getComputedStyle(projectList).overflowY : "",
      threadOverflowY: threadTimeline ? window.getComputedStyle(threadTimeline).overflowY : "",
      rightOverflow,
    };
  });

  expect(containment.documentScrollWidth).toBeLessThanOrEqual(containment.viewportWidth);
  expect(containment.rightOverflow).toEqual([]);
  expect(containment.documentScrollHeight).toBeLessThanOrEqual(containment.viewportHeight * 1.75);
  expect(containment.homeHeight).toBeLessThanOrEqual(containment.viewportHeight * 1.65);
  expect(containment.projectScrollHeight).toBeGreaterThan(containment.projectClientHeight + 120);
  expect(containment.threadScrollHeight).toBeGreaterThan(containment.threadClientHeight + 120);
  expect(containment.projectOverflowY).toBe("auto");
  expect(containment.threadOverflowY).toBe("auto");

  const projectList = page.locator(".ct-ai-home__project-list");
  await projectList.hover();
  const windowScrollBeforeProjectWheel = await page.evaluate(() => window.scrollY);
  await page.mouse.wheel(0, 900);
  await expect.poll(() => projectList.evaluate((element) => element.scrollTop)).toBeGreaterThan(80);
  await expect
    .poll(() => page.evaluate((before) => Math.abs(window.scrollY - before), windowScrollBeforeProjectWheel))
    .toBeLessThan(5);

  const threadTimeline = page.locator(".ct-thread-timeline");
  await threadTimeline.hover();
  const windowScrollBeforeThreadWheel = await page.evaluate(() => window.scrollY);
  await page.mouse.wheel(0, 1200);
  await expect.poll(() => threadTimeline.evaluate((element) => element.scrollTop)).toBeGreaterThan(120);
  await expect
    .poll(() => page.evaluate((before) => Math.abs(window.scrollY - before), windowScrollBeforeThreadWheel))
    .toBeLessThan(5);
});

test("AI home windows very large project lists and keeps search access", async ({ page }) => {
  const workspaces = Array.from({ length: 240 }, (_, index) => ({
    id: `ws-window-home-${index + 1}`,
    name: index === 199 ? "SPDK archived deep target 200" : `SPDK archived project ${index + 1}`,
    repo_path: `/Volumes/Media/dpdk/spdk-window-${index + 1}`,
    indexed: 1,
    index_job: null,
    index_progress: 100,
    analyze_status: null,
    analyze_progress: 0,
    last_index_error: null,
    created_at: "2026-06-28T00:00:00Z",
    updated_at: `2026-06-28T00:${String(index % 60).padStart(2, "0")}:00Z`,
    materials: [],
    reports: [],
  }));
  const threads = Array.from({ length: 12 }, (_, index) => ({
    id: `conv-window-home-${index + 1}`,
    scope_type: "workspace",
    scope_id: "ws-window-home-1",
    workspace_id: "ws-window-home-1",
    memory_namespace: "workspace:ws-window-home-1",
    title: `SPDK windowed thread ${index + 1}`,
    status: "idle",
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
  await expect(page.locator(".ct-thread-project")).toHaveCount(80);
  await expect(page.getByText("SPDK archived deep target 200")).toHaveCount(0);

  const projectList = page.locator(".ct-ai-home__project-list");
  const initialMetrics = await projectList.evaluate((element) => ({
    childCount: element.children.length,
    scrollHeight: element.scrollHeight,
    clientHeight: element.clientHeight,
  }));
  expect(initialMetrics.childCount).toBe(80);
  expect(initialMetrics.scrollHeight).toBeGreaterThan(initialMetrics.clientHeight);

  await page.getByLabel("搜索项目").hover();
  await page.getByLabel("搜索项目").fill("target 200");
  await expect(page.locator(".ct-thread-project")).toHaveCount(1);
  await expect(page.getByText("SPDK archived deep target 200")).toBeVisible();
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
  await page.route("**/api/settings/agent-runtimes", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [] } });
  });
  await page.route("**/api/ai/conversations?workspace_id=ws-diag&limit=50", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [] } });
  });
  await page.route("**/api/ai/conversations?limit=100", async (route) => {
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
  await page.route("**/api/ai/conversations/conv-diag/events?**", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        items: [
          {
            event_id: 1,
            run_id: "run-diag",
            conversation_id: "conv-diag",
            event_type: "status",
            payload: { status: "running", message: "正在准备工作区源码上下文" },
            created_at: "2026-06-28T00:00:01Z",
          },
        ],
      },
    });
  });
  await page.route("**/api/ai/conversations/conv-diag/stream?cursor=0", async (route) => {
    completed = true;
    const diagnostics = Array.from({ length: 20 }, (_, index) => {
      const step = String(index + 1).padStart(2, "0");
      return [
        `data: {"event_id":${index + 2},"run_id":"run-diag","conversation_id":"conv-diag","event_type":"delta","payload":{"kind":"diagnostic","content":"诊断步骤 ${step}：正在读取 lib/nvmf/connect.c"},"created_at":"2026-06-28T00:00:01Z"}`,
        "",
      ].join("\n");
    });
    await route.fulfill({
      headers: {
        ...jsonHeaders(route.request().headers().origin),
        "Content-Type": "text/event-stream",
      },
      body: [
        'data: {"event_id":1,"run_id":"run-diag","conversation_id":"conv-diag","event_type":"status","payload":{"status":"running","message":"正在准备工作区源码上下文"},"created_at":"2026-06-28T00:00:01Z"}',
        "",
        'data: {"event_id":101,"run_id":"run-diag","conversation_id":"conv-diag","event_type":"status","payload":{"status":"running","message":"正在准备工作区源码上下文"},"created_at":"2026-06-28T00:00:01Z"}',
        "",
        ...diagnostics,
        'data: {"event_id":102,"run_id":"run-diag","conversation_id":"conv-diag","event_type":"delta","payload":{"kind":"diagnostic","content":"诊断步骤 01：正在读取 lib/nvmf/connect.c"},"created_at":"2026-06-28T00:00:01Z"}',
        "",
        'data: {"event_id":22,"run_id":"run-diag","conversation_id":"conv-diag","event_type":"delta","payload":{"content":"最终答案：覆盖 reconnect timeout 的黑盒观察点。"},"created_at":"2026-06-28T00:00:02Z"}',
        "",
        'data: {"event_id":23,"run_id":"run-diag","conversation_id":"conv-diag","event_type":"done","payload":{},"created_at":"2026-06-28T00:00:03Z"}',
        "",
      ].join("\n"),
    });
  });

  await page.goto("/ai/conv-diag", { waitUntil: "domcontentloaded" });

  await expect(page.getByText("最终答案：覆盖 reconnect timeout 的黑盒观察点。")).toBeVisible();
  await expect(page.locator(".ct-codex-ai__reader")).not.toContainText("正在准备工作区源码上下文");
  await expect(page.locator(".ct-codex-ai__reader")).not.toContainText("诊断步骤 01");
  const agentStatusPanel = page.getByTestId("agent-status-panel");
  await expect(agentStatusPanel.getByText("Agent 状态")).toBeVisible();
  await expect(agentStatusPanel.getByText("Thinking")).toBeVisible();
  await expect(agentStatusPanel.getByText("CLI 气泡")).toBeVisible();
  await expect(agentStatusPanel.getByText("默认折叠").first()).toBeVisible();
  await expect(agentStatusPanel.getByText("Session")).toBeVisible();
  await expect(agentStatusPanel.getByText("内置上下文")).toBeVisible();
  await expect(agentStatusPanel.getByText(/最新过程：/)).toBeVisible();
  await expect(agentStatusPanel.locator("p").filter({ hasText: "诊断步骤 01：正在读取 lib/nvmf/connect.c" })).toBeHidden();
  const processDisclosure = page.getByTestId("agent-process-disclosure");
  await expect(processDisclosure.getByText("Agent 过程")).toBeVisible();
  await expect(processDisclosure.locator("summary")).toContainText("默认折叠");
  await expect(processDisclosure.locator("p").filter({ hasText: "正在准备工作区源码上下文" })).toBeHidden();
  await expect(
    processDisclosure.locator("p").filter({ hasText: "诊断步骤 01：正在读取 lib/nvmf/connect.c" }),
  ).toBeHidden();
  await processDisclosure.getByText("Agent 过程").click();
  await expect(processDisclosure.getByText("正在准备工作区源码上下文")).toBeVisible();
  await expect(
    processDisclosure.locator("p").filter({ hasText: "诊断步骤 01：正在读取 lib/nvmf/connect.c" }),
  ).toBeVisible();
  await expect(
    processDisclosure.locator("p").filter({ hasText: "诊断步骤 20：正在读取 lib/nvmf/connect.c" }),
  ).toBeVisible();
  await agentStatusPanel.getByText(/最新过程：/).click();
  await expect(agentStatusPanel.locator("p").filter({ hasText: "诊断步骤 20：正在读取 lib/nvmf/connect.c" })).toBeVisible();
  await expect(processDisclosure.locator("p").filter({ hasText: "正在准备工作区源码上下文" })).toHaveCount(1);
  await expect(processDisclosure.locator("p").filter({ hasText: "诊断步骤 01：正在读取 lib/nvmf/connect.c" })).toHaveCount(1);

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出" }).click();
  const download = await downloadPromise;
  const exportPath = test.info().outputPath("ai-thread-diagnostic-export.md");
  await download.saveAs(exportPath);
  const exported = fs.readFileSync(exportPath, "utf8");
  expect(exported).toContain("最终答案：覆盖 reconnect timeout 的黑盒观察点。");
  expect(exported).not.toContain("正在准备工作区源码上下文");
  expect(exported).not.toContain("诊断步骤 01");
});

test("AI conversation exposes Clowder-style lifecycle status and artifact-first delivery", async ({ page }) => {
  await page.route("**/api/workspaces", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: [
        {
          id: "ws-clowder-parity",
          name: "SPDK Clowder 对齐项目",
          repo_path: "/Volumes/Media/dpdk/spdk",
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
  const agentRuntime = {
    id: "agent-claude",
    name: "Claude Code",
    command: "claude",
    args: [],
    prompt_transport: "claude_print_arg",
    output_mode: "stream_json",
    working_dir_mode: "project",
    fixed_working_dir: "",
    env: {},
    health_command: "",
    timeout_seconds: 900,
    completion_mode: "process_exit",
    idle_complete_seconds: 5,
    sentinel_text: "",
    session_persistence: "resume_args",
    resume_args: [],
    enabled: true,
    created_at: "2026-06-28T00:00:00Z",
    updated_at: "2026-06-28T00:00:00Z",
  };
  await page.route("**/api/settings/agent-runtimes?enabled=true", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [agentRuntime] } });
  });
  await page.route("**/api/settings/agent-runtimes", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [agentRuntime] } });
  });
  await page.route("**/api/ai/conversations?workspace_id=ws-clowder-parity&limit=50", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [] } });
  });
  await page.route("**/api/ai/conversations?limit=100", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [] } });
  });
  await page.route("**/api/ai/conversations/conv-clowder-parity", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        id: "conv-clowder-parity",
        scope_type: "workspace",
        scope_id: "ws-clowder-parity",
        workspace_id: "ws-clowder-parity",
        memory_namespace: "workspace:ws-clowder-parity",
        runtime_type: "agent_runtime",
        agent_runtime_id: "agent-claude",
        title: "Clowder 对齐线程",
        status: "idle",
        initial_context: {},
        created_at: "2026-06-28T00:00:00Z",
        updated_at: "2026-06-28T00:00:42Z",
        latest_run: {
          id: "run-clowder-parity",
          conversation_id: "conv-clowder-parity",
          status: "completed",
          cursor: 12,
          error: null,
          model: "agent:Claude Code",
          token_usage: {},
          created_at: "2026-06-28T00:00:00Z",
          started_at: "2026-06-28T00:00:02Z",
          completed_at: "2026-06-28T00:00:42Z",
        },
      },
    });
  });
  await page.route("**/api/ai/conversations/conv-clowder-parity/messages", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        items: [
          {
            id: "msg-clowder-user",
            conversation_id: "conv-clowder-parity",
            run_id: "run-clowder-parity",
            role: "user",
            content: "完整生成代码分析、流程梳理、SFMEA、黑盒测试用例",
            references: [],
            actions: [],
            created_at: "2026-06-28T00:00:01Z",
          },
          {
            id: "msg-clowder-assistant",
            conversation_id: "conv-clowder-parity",
            run_id: "run-clowder-parity",
            role: "assistant",
            content: "## SPDK 测试设计\n\n已生成结构化产物（42 条步骤/用例），正文只展示摘要。\n\n---\n完整测试设计/SFMEA/黑盒用例已保存为下载产物。",
            references: [],
            actions: [
              {
                id: "download_run_artifact",
                label: "下载完整产物",
                href: "/api/ai/conversations/conv-clowder-parity/runs/run-clowder-parity/artifact",
                kind: "download",
              },
            ],
            created_at: "2026-06-28T00:00:42Z",
          },
        ],
      },
    });
  });
  await page.route("**/api/ai/conversations/conv-clowder-parity/events?**", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        items: [
          {
            event_id: 1,
            run_id: "run-clowder-parity",
            conversation_id: "conv-clowder-parity",
            event_type: "status",
            payload: { status: "running", message: "正在读取 GitNexus/CGC 图谱产物、工作区源码上下文。" },
            created_at: "2026-06-28T00:00:02Z",
          },
          {
            event_id: 2,
            run_id: "run-clowder-parity",
            conversation_id: "conv-clowder-parity",
            event_type: "delta",
            payload: { kind: "diagnostic", content: "CodeTalk 已启动 Claude Code。" },
            created_at: "2026-06-28T00:00:03Z",
          },
          {
            event_id: 3,
            run_id: "run-clowder-parity",
            conversation_id: "conv-clowder-parity",
            event_type: "delta",
            payload: { kind: "diagnostic", content: "会话已延续：沿用当前线程的 Agent 上下文。" },
            created_at: "2026-06-28T00:00:04Z",
          },
          {
            event_id: 4,
            run_id: "run-clowder-parity",
            conversation_id: "conv-clowder-parity",
            event_type: "delta",
            payload: { kind: "diagnostic", content: "下载产物已准备：约 48000 bytes，正文区仅保留摘要。" },
            created_at: "2026-06-28T00:00:42Z",
          },
        ],
      },
    });
  });

  await page.goto("/ai/conv-clowder-parity", { waitUntil: "domcontentloaded" });

  const agentStatusPanel = page.getByTestId("agent-status-panel");
  await expect(agentStatusPanel.getByText("生命周期")).toBeVisible();
  await expect(agentStatusPanel.getByText("产物就绪")).toBeVisible();
  await expect(agentStatusPanel.getByText("耗时")).toBeVisible();
  await expect(agentStatusPanel.getByText("40 秒")).toBeVisible();
  await expect(agentStatusPanel.getByText("自动续接")).toBeVisible();
  await expect(agentStatusPanel.getByText("取消/失败")).toBeVisible();
  await expect(agentStatusPanel.getByText("无阻塞")).toBeVisible();
  await expect(agentStatusPanel.locator(".ct-ai-agent-status strong").filter({ hasText: "run-clowder-parity" })).toBeVisible();
  await expect(page.locator(".ct-codex-ai__reader")).toContainText("已生成结构化产物");
  await expect(page.locator(".ct-codex-ai__reader")).not.toContainText("CodeTalk 已启动 Claude Code");
  await expect(page.getByText("附件与产物")).toBeVisible();
  await expect(page.getByRole("link", { name: /下载完整产物/ })).toBeVisible();
});

test("AI conversation keeps raw tool output out of the collapsed Agent process summary", async ({ page }) => {
  await page.route("**/api/workspaces", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: [
        {
          id: "ws-agent-summary",
          name: "SPDK 摘要项目",
          repo_path: "/Volumes/Media/dpdk/spdk",
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
  await page.route("**/api/settings/agent-runtimes", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [] } });
  });
  await page.route("**/api/ai/conversations?workspace_id=ws-agent-summary&limit=50", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [] } });
  });
  await page.route("**/api/ai/conversations?limit=100", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [] } });
  });
  await page.route("**/api/ai/conversations/conv-agent-summary", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        id: "conv-agent-summary",
        scope_type: "workspace",
        scope_id: "ws-agent-summary",
        workspace_id: "ws-agent-summary",
        memory_namespace: "workspace:ws-agent-summary",
        title: "Agent 过程摘要线程",
        status: "idle",
        initial_context: {},
        created_at: "2026-06-28T00:00:00Z",
        updated_at: "2026-06-28T00:00:02Z",
        latest_run: {
          id: "run-agent-summary",
          conversation_id: "conv-agent-summary",
          status: "completed",
          cursor: 6,
          error: null,
          model: "agent:Claude Code",
          token_usage: {},
          created_at: "2026-06-28T00:00:01Z",
          started_at: "2026-06-28T00:00:01Z",
          completed_at: "2026-06-28T00:00:02Z",
        },
      },
    });
  });
  await page.route("**/api/ai/conversations/conv-agent-summary/messages", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        items: [
          {
            id: "msg-agent-summary-user",
            conversation_id: "conv-agent-summary",
            run_id: "run-agent-summary",
            role: "user",
            content: "针对 iSCSI 登录生成黑盒用例",
            references: [],
            actions: [],
            created_at: "2026-06-28T00:00:01Z",
          },
          {
            id: "msg-agent-summary-assistant",
            conversation_id: "conv-agent-summary",
            run_id: "run-agent-summary",
            role: "assistant",
            content: "## 黑盒测试用例\n\n已生成结构化产物，正文保持干净。",
            references: [],
            actions: [],
            created_at: "2026-06-28T00:00:02Z",
          },
        ],
      },
    });
  });
  await page.route("**/api/ai/conversations/conv-agent-summary/events?**", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        items: [
          {
            event_id: 1,
            run_id: "run-agent-summary",
            conversation_id: "conv-agent-summary",
            event_type: "status",
            payload: { status: "running", message: "正在读取工作区源码上下文。" },
            created_at: "2026-06-28T00:00:01Z",
          },
          {
            event_id: 2,
            run_id: "run-agent-summary",
            conversation_id: "conv-agent-summary",
            event_type: "delta",
            payload: {
              kind: "diagnostic",
              content: 'Bash {"command":"grep -n \\"status_detail\\" lib/iscsi/iscsi.c | head"}',
            },
            created_at: "2026-06-28T00:00:01Z",
          },
          {
            event_id: 3,
            run_id: "run-agent-summary",
            conversation_id: "conv-agent-summary",
            event_type: "delta",
            payload: {
              kind: "diagnostic",
              content: "1434:\t\trsph->status_detail = ISCSI_LOGIN_TARGET_TEMPORARILY_MOVED;",
            },
            created_at: "2026-06-28T00:00:01Z",
          },
        ],
      },
    });
  });

  await page.goto("/ai/conv-agent-summary", { waitUntil: "domcontentloaded" });

  const processDisclosure = page.getByTestId("agent-process-disclosure");
  await expect(processDisclosure.getByText("Agent 过程")).toBeVisible();
  await expect(processDisclosure.locator("summary")).toContainText("正在读取工作区源码上下文");
  await expect(processDisclosure.locator("summary")).not.toContainText("1434:");
  await expect(processDisclosure.locator("summary")).not.toContainText("rsph->status_detail");
  await expect(page.locator(".ct-codex-ai__reader")).not.toContainText("rsph->status_detail");

  await processDisclosure.getByText("Agent 过程").click();
  await expect(processDisclosure.getByText("rsph->status_detail")).toBeVisible();
});

test("AI conversation collapsed Agent summary prefers friendly progress over raw source tail", async ({ page }) => {
  await page.route("**/api/workspaces", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: [
        {
          id: "ws-agent-friendly-summary",
          name: "SPDK 友好过程摘要",
          repo_path: "/Volumes/Media/dpdk/spdk",
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
  await page.route("**/api/settings/agent-runtimes", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [] } });
  });
  await page.route("**/api/ai/conversations?workspace_id=ws-agent-friendly-summary&limit=50", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [] } });
  });
  await page.route("**/api/ai/conversations?limit=100", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [] } });
  });
  await page.route("**/api/ai/conversations/conv-agent-friendly-summary", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        id: "conv-agent-friendly-summary",
        scope_type: "workspace",
        scope_id: "ws-agent-friendly-summary",
        workspace_id: "ws-agent-friendly-summary",
        memory_namespace: "workspace:ws-agent-friendly-summary",
        title: "Agent 友好过程摘要线程",
        status: "idle",
        initial_context: {},
        created_at: "2026-06-28T00:00:00Z",
        updated_at: "2026-06-28T00:00:02Z",
        latest_run: {
          id: "run-agent-friendly-summary",
          conversation_id: "conv-agent-friendly-summary",
          status: "completed",
          cursor: 6,
          error: null,
          model: "agent:Claude Code",
          token_usage: {},
          created_at: "2026-06-28T00:00:01Z",
          started_at: "2026-06-28T00:00:01Z",
          completed_at: "2026-06-28T00:00:02Z",
        },
      },
    });
  });
  await page.route("**/api/ai/conversations/conv-agent-friendly-summary/messages", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        items: [
          {
            id: "msg-agent-friendly-user",
            conversation_id: "conv-agent-friendly-summary",
            run_id: "run-agent-friendly-summary",
            role: "user",
            content: "核对 iSCSI CHAP 源码证据",
            references: [],
            actions: [],
            created_at: "2026-06-28T00:00:01Z",
          },
          {
            id: "msg-agent-friendly-assistant",
            conversation_id: "conv-agent-friendly-summary",
            run_id: "run-agent-friendly-summary",
            role: "assistant",
            content: "FINAL_ANSWER: 已基于源码核对完成。",
            references: [],
            actions: [],
            created_at: "2026-06-28T00:00:02Z",
          },
        ],
      },
    });
  });
  await page.route("**/api/ai/conversations/conv-agent-friendly-summary/events?**", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        items: [
          {
            event_id: 1,
            run_id: "run-agent-friendly-summary",
            conversation_id: "conv-agent-friendly-summary",
            event_type: "status",
            payload: { status: "running", message: "正在读取工作区源码上下文。" },
            created_at: "2026-06-28T00:00:01Z",
          },
          {
            event_id: 2,
            run_id: "run-agent-friendly-summary",
            conversation_id: "conv-agent-friendly-summary",
            event_type: "delta",
            payload: {
              kind: "diagnostic",
              content: "CodeTalk 已在完成时整理执行器输出，最终回答以线程消息为准。",
            },
            created_at: "2026-06-28T00:00:02Z",
          },
          {
            event_id: 3,
            run_id: "run-agent-friendly-summary",
            conversation_id: "conv-agent-friendly-summary",
            event_type: "delta",
            payload: { kind: "diagnostic", content: "213\t\t}" },
            created_at: "2026-06-28T00:00:02Z",
          },
        ],
      },
    });
  });

  await page.goto("/ai/conv-agent-friendly-summary", { waitUntil: "domcontentloaded" });

  const processDisclosure = page.getByTestId("agent-process-disclosure");
  await expect(processDisclosure.locator("summary")).toContainText("CodeTalk 已在完成时整理执行器输出");
  await expect(processDisclosure.locator("summary")).not.toContainText("213");
});

test("AI conversation keeps the start and end of a long Agent process history", async ({ page }) => {
  await page.route("**/api/workspaces", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: [
        {
          id: "ws-agent-long-process",
          name: "SPDK 长过程项目",
          repo_path: "/Volumes/Media/dpdk/spdk",
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
  await page.route("**/api/settings/agent-runtimes", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [] } });
  });
  await page.route("**/api/ai/conversations?workspace_id=ws-agent-long-process&limit=50", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [] } });
  });
  await page.route("**/api/ai/conversations?limit=100", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [] } });
  });
  await page.route("**/api/ai/conversations/conv-agent-long-process", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        id: "conv-agent-long-process",
        scope_type: "workspace",
        scope_id: "ws-agent-long-process",
        workspace_id: "ws-agent-long-process",
        memory_namespace: "workspace:ws-agent-long-process",
        title: "Agent 长过程线程",
        status: "idle",
        initial_context: {},
        created_at: "2026-06-28T00:00:00Z",
        updated_at: "2026-06-28T00:00:02Z",
        latest_run: {
          id: "run-agent-long-process",
          conversation_id: "conv-agent-long-process",
          status: "completed",
          cursor: 300,
          error: null,
          model: "agent:Claude Code",
          token_usage: {},
          created_at: "2026-06-28T00:00:01Z",
          started_at: "2026-06-28T00:00:01Z",
          completed_at: "2026-06-28T00:00:02Z",
        },
      },
    });
  });
  await page.route("**/api/ai/conversations/conv-agent-long-process/messages", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        items: [
          {
            id: "msg-agent-long-process-user",
            conversation_id: "conv-agent-long-process",
            run_id: "run-agent-long-process",
            role: "user",
            content: "分析 SPDK iSCSI login 并生成黑盒测试",
            references: [],
            actions: [],
            created_at: "2026-06-28T00:00:01Z",
          },
          {
            id: "msg-agent-long-process-assistant",
            conversation_id: "conv-agent-long-process",
            run_id: "run-agent-long-process",
            role: "assistant",
            content: "## 结论\n\nLONG_PROCESS_FINAL: 正文只展示最终答案。",
            references: [],
            actions: [],
            created_at: "2026-06-28T00:00:02Z",
          },
        ],
      },
    });
  });
  await page.route("**/api/ai/conversations/conv-agent-long-process/events?**", async (route) => {
    const items = Array.from({ length: 260 }, (_, index) => ({
      event_id: index + 1,
      run_id: "run-agent-long-process",
      conversation_id: "conv-agent-long-process",
      event_type: index === 0 ? "status" : "delta",
      payload:
        index === 0
          ? { status: "running", message: "AGENT_SPAWN_START: resume session and load workspace" }
          : {
              kind: "diagnostic",
              content: `AGENT_PROCESS_STEP_${String(index + 1).padStart(3, "0")}: reading lib/iscsi/iscsi.c`,
            },
      created_at: "2026-06-28T00:00:01Z",
    }));
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: { items },
    });
  });

  await page.goto("/ai/conv-agent-long-process", { waitUntil: "domcontentloaded" });

  await expect(page.getByText("LONG_PROCESS_FINAL: 正文只展示最终答案。")).toBeVisible();
  await expect(page.locator(".ct-codex-ai__reader")).not.toContainText("AGENT_PROCESS_STEP_120");
  const processDisclosure = page.getByTestId("agent-process-disclosure");
  await expect(processDisclosure.getByText("Agent 过程")).toBeVisible();
  await expect(processDisclosure.locator("summary")).toContainText("默认折叠");

  await processDisclosure.getByText("Agent 过程").click();
  await expect(processDisclosure.getByText("AGENT_SPAWN_START: resume session and load workspace")).toBeVisible();
  await expect(
    processDisclosure.locator("p").filter({ hasText: "AGENT_PROCESS_STEP_260: reading lib/iscsi/iscsi.c" }),
  ).toBeVisible();
  await expect(processDisclosure.getByText(/已折叠中间 \d+ 条 Agent 过程事件/)).toBeVisible();
  await expect(
    processDisclosure.locator("p").filter({ hasText: "AGENT_PROCESS_STEP_070: reading lib/iscsi/iscsi.c" }),
  ).toHaveCount(0);
});

test("AI conversation keeps long structured artifacts compact while streaming", async ({ page }) => {
  let completed = false;
  let releaseDone = () => {};
  let streamRequestedResolve: (() => void) | null = null;
  const streamRequested = new Promise<void>((resolve) => {
    streamRequestedResolve = resolve;
  });
  const doneGate = new Promise<void>((resolve) => {
    releaseDone = resolve;
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
        'data: {"event_id":1,"run_id":"run-artifact","conversation_id":"conv-artifact","event_type":"delta","payload":{"kind":"artifact_progress","content":"正在生成结构化产物，完成后会提供下载文件。"},"created_at":"2026-06-28T00:00:02Z"}',
        "",
        "",
      ].join("\n"),
    );
    await doneGate;
    completed = true;
    res.write(
      [
        'data: {"event_id":2,"run_id":"run-artifact","conversation_id":"conv-artifact","event_type":"done","payload":{},"created_at":"2026-06-28T00:00:03Z"}',
        "",
        "",
      ].join("\n"),
    );
    res.end();
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as AddressInfo).port;

  await page.route("**/api/workspaces", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: [
        {
          id: "ws-artifact",
          name: "SPDK 产物项目",
          repo_path: "/Volumes/Media/dpdk/spdk",
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
  await page.route("**/api/ai/conversations?workspace_id=ws-artifact&limit=50", async (route) => {
    await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [] } });
  });
  await page.route("**/api/ai/conversations/conv-artifact", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        id: "conv-artifact",
        scope_type: "workspace",
        scope_id: "ws-artifact",
        workspace_id: "ws-artifact",
        memory_namespace: "workspace:ws-artifact",
        title: "SPDK SFMEA 线程",
        status: completed ? "idle" : "running",
        initial_context: {},
        created_at: "2026-06-28T00:00:00Z",
        updated_at: "2026-06-28T00:00:00Z",
        latest_run: {
          id: "run-artifact",
          conversation_id: "conv-artifact",
          status: completed ? "completed" : "running",
          cursor: completed ? 2 : 0,
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
  await page.route("**/api/ai/conversations/conv-artifact/messages", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: {
        items: [
          {
            id: "msg-artifact-user",
            conversation_id: "conv-artifact",
            run_id: "run-artifact",
            role: "user",
            content: "生成完整 SFMEA 和黑盒测试用例",
            references: [],
            actions: [],
            created_at: "2026-06-28T00:00:01Z",
          },
          ...(completed
            ? [
                {
                  id: "msg-artifact-assistant",
                  conversation_id: "conv-artifact",
                  run_id: "run-artifact",
                  role: "assistant",
                  content: "已生成完整结构化产物，可下载查看 SFMEA 和黑盒测试用例。",
                  references: [],
                  actions: [
                    {
                      id: "download_run_artifact",
                      label: "下载完整产物",
                      href: "/api/ai/conversations/conv-artifact/runs/run-artifact/artifact",
                      kind: "download",
                    },
                  ],
                  created_at: "2026-06-28T00:00:03Z",
                },
              ]
            : []),
        ],
      },
    });
  });
  await page.route("**/api/ai/conversations/conv-artifact/stream?cursor=0", async (route) => {
    await route.continue({ url: `http://127.0.0.1:${port}/stream` });
  });

  try {
    await page.goto("/ai/conv-artifact", { waitUntil: "domcontentloaded" });
    await streamRequested;

    const reader = page.getByLabel("AI 线程对话内容");
    await expect(page.getByText("正在生成结构化产物，完成后会提供下载文件。")).toBeVisible();
    await expect(reader).not.toContainText("TC-09");
    await expect(reader).not.toContainText("SFMEA 风险 3");

    releaseDone();
    await expect(page.getByText("已生成完整结构化产物，可下载查看 SFMEA 和黑盒测试用例。")).toBeVisible();
    const artifactCard = page.locator(".ct-codex-message__actions").filter({ hasText: "下载完整产物" });
    await expect(artifactCard).toBeVisible();
    await expect(artifactCard).toContainText("附件与产物");
    await expect(page.getByRole("link", { name: /下载完整产物/ })).toBeVisible();
    const cardMetrics = await artifactCard.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      const link = element.querySelector("a") as HTMLElement | null;
      const linkRect = link?.getBoundingClientRect();
      return {
        display: window.getComputedStyle(element).display,
        width: rect.width,
        linkWidth: linkRect?.width ?? 0,
        overflowing: element.scrollWidth > element.clientWidth + 1,
      };
    });
    expect(cardMetrics.display).toBe("grid");
    expect(cardMetrics.linkWidth).toBeGreaterThan(180);
    expect(cardMetrics.overflowing).toBe(false);
    await expect(reader).not.toContainText("TC-09");
    await expect(reader).not.toContainText("SFMEA 风险 3");
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
