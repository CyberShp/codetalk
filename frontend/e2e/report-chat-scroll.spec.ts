import { expect, test } from "@playwright/test";
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

test("report AI assistant preserves reading position while streaming", async ({ page }) => {
  let releaseSecondChunk: (() => void) | null = null;
  const secondChunkGate = new Promise<void>((resolve) => {
    releaseSecondChunk = resolve;
  });

  const server = http.createServer(async (_req, res) => {
    res.writeHead(200, {
      "Access-Control-Allow-Origin": frontendOrigin,
      "Access-Control-Allow-Credentials": "true",
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    });
    res.write('data: {"content":"第一段报告助手流式回答。\\n\\n","done":false}\n\n');
    await secondChunkGate;
    res.write('data: {"content":"第二段到达时不应把阅读位置拉到底部。\\n\\n","done":false}\n\n');
    await new Promise((resolve) => setTimeout(resolve, 200));
    res.write('data: {"done":true}\n\n');
    res.end();
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as AddressInfo).port;

  const longAssistant = Array.from({ length: 34 }, (_, index) =>
    `历史报告助手回答 ${index + 1}：覆盖异常路径、恢复动作、日志观测点和测试设计。`,
  ).join("\n\n");

  try {
    await page.route("**/api/workspaces", async (route) => {
      await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: [] });
    });
    await page.route("**/api/ai/conversations?limit=3", async (route) => {
      await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [] } });
    });
    await page.route("**/api/tasks/report-scroll", async (route) => {
      await route.fulfill({
        headers: jsonHeaders(route.request().headers().origin),
        json: {
          id: "report-scroll",
          status: "completed",
          repo_id: "repo-1",
          type: "analysis",
          created_at: "2026-07-01T00:00:00Z",
          updated_at: "2026-07-01T00:00:00Z",
        },
      });
    });
    await page.route("**/api/tasks/report-scroll/output", async (route) => {
      await route.fulfill({
        headers: jsonHeaders(route.request().headers().origin),
        json: [{ filename: "01-项目与模块地图.md", size: 80 }],
      });
    });
    await page.route("**/api/tasks/report-scroll/output/01-%E9%A1%B9%E7%9B%AE%E4%B8%8E%E6%A8%A1%E5%9D%97%E5%9C%B0%E5%9B%BE.md", async (route) => {
      await route.fulfill({
        headers: jsonHeaders(route.request().headers().origin),
        json: {
          filename: "01-项目与模块地图.md",
          content: "# 报告正文\n\n这是用于验证报告助手滚动稳定性的报告。",
        },
      });
    });
    await page.route("**/api/tasks/report-scroll/chat", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          headers: jsonHeaders(route.request().headers().origin),
          json: [
            {
              id: 1,
              task_id: "report-scroll",
              role: "user",
              content: "先生成长历史回答",
              created_at: "2026-07-01T00:00:00Z",
            },
            {
              id: 2,
              task_id: "report-scroll",
              role: "assistant",
              content: longAssistant,
              created_at: "2026-07-01T00:00:01Z",
            },
          ],
        });
        return;
      }
      await route.continue({ url: `http://127.0.0.1:${port}/stream` });
    });

    await page.setViewportSize({ width: 1440, height: 760 });
    await page.goto("/tasks/report-scroll/report", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: "分析报告" })).toBeVisible();

    const reader = page.getByLabel("报告 AI 助手对话内容");
    await expect
      .poll(() => reader.evaluate((element) => element.scrollHeight > element.clientHeight * 2))
      .toBeTruthy();
    await expect
      .poll(() => reader.evaluate((element) => element.scrollHeight - element.clientHeight - element.scrollTop))
      .toBeLessThan(120);

    await page.getByPlaceholder("提问关于此报告的问题...").fill("继续分析，但我会向上阅读历史");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("第一段报告助手流式回答。")).toBeVisible();

    const readerBox = await reader.boundingBox();
    expect(readerBox).not.toBeNull();
    await page.mouse.move(readerBox!.x + readerBox!.width / 2, readerBox!.y + readerBox!.height / 2);
    const beforeUserScroll = await reader.evaluate((element) => element.scrollTop);
    await page.mouse.wheel(0, -1200);
    await expect.poll(() => reader.evaluate((element) => element.scrollTop)).toBeLessThan(beforeUserScroll - 80);
    const userScrollTop = await reader.evaluate((element) => element.scrollTop);
    await expect(page.getByRole("button", { name: "跳到最新回复" })).toBeVisible();

    releaseSecondChunk?.();
    await expect(page.getByText("第二段到达时不应把阅读位置拉到底部。")).toBeVisible();
    await page.waitForTimeout(100);

    const afterSecondChunk = await reader.evaluate((element) => ({
      scrollTop: element.scrollTop,
      distanceFromBottom: element.scrollHeight - element.clientHeight - element.scrollTop,
    }));
    expect(Math.abs(afterSecondChunk.scrollTop - userScrollTop)).toBeLessThan(90);
    expect(afterSecondChunk.distanceFromBottom).toBeGreaterThan(180);

    await page.getByRole("button", { name: "跳到最新回复" }).hover();
    await page.getByRole("button", { name: "跳到最新回复" }).click();
    await expect
      .poll(() => reader.evaluate((element) => element.scrollHeight - element.clientHeight - element.scrollTop))
      .toBeLessThan(120);
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

test("report AI assistant avoids continuous decorative loading animations", async ({ page }) => {
  let releaseStream: (() => void) | null = null;
  const streamGate = new Promise<void>((resolve) => {
    releaseStream = resolve;
  });

  const server = http.createServer(async (_req, res) => {
    res.writeHead(200, {
      "Access-Control-Allow-Origin": frontendOrigin,
      "Access-Control-Allow-Credentials": "true",
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    });
    await streamGate;
    res.write('data: {"content":"报告助手最终回答。","done":false}\n\n');
    res.write('data: {"done":true}\n\n');
    res.end();
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as AddressInfo).port;

  try {
    await page.route("**/api/workspaces", async (route) => {
      await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: [] });
    });
    await page.route("**/api/ai/conversations?limit=3", async (route) => {
      await route.fulfill({ headers: jsonHeaders(route.request().headers().origin), json: { items: [] } });
    });
    await page.route("**/api/tasks/report-animation", async (route) => {
      await route.fulfill({
        headers: jsonHeaders(route.request().headers().origin),
        json: {
          id: "report-animation",
          status: "completed",
          repo_id: "repo-1",
          type: "analysis",
          created_at: "2026-07-01T00:00:00Z",
          updated_at: "2026-07-01T00:00:00Z",
        },
      });
    });
    await page.route("**/api/tasks/report-animation/output", async (route) => {
      await route.fulfill({
        headers: jsonHeaders(route.request().headers().origin),
        json: [{ filename: "01-项目与模块地图.md", size: 80 }],
      });
    });
    await page.route("**/api/tasks/report-animation/output/01-%E9%A1%B9%E7%9B%AE%E4%B8%8E%E6%A8%A1%E5%9D%97%E5%9C%B0%E5%9B%BE.md", async (route) => {
      await route.fulfill({
        headers: jsonHeaders(route.request().headers().origin),
        json: {
          filename: "01-项目与模块地图.md",
          content: "# 报告正文\n\n这是用于验证报告助手动画预算的报告。",
        },
      });
    });
    await page.route("**/api/tasks/report-animation/chat", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          headers: jsonHeaders(route.request().headers().origin),
          json: [],
        });
        return;
      }
      await route.continue({ url: `http://127.0.0.1:${port}/stream` });
    });

    await page.setViewportSize({ width: 1440, height: 760 });
    await page.goto("/tasks/report-animation/report", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: "分析报告" })).toBeVisible();

    await page.getByPlaceholder("提问关于此报告的问题...").fill("开始生成但不要用装饰性连续动画");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("正在生成回答")).toBeVisible();

    const runningDecorativeAnimations = await page.evaluate(() =>
      document
        .getAnimations()
        .filter((animation) => {
          const target = animation.effect instanceof KeyframeEffect ? animation.effect.target : null;
          return target instanceof Element && !target.closest(".animate-spin");
        })
        .map((animation) => {
          const target = animation.effect instanceof KeyframeEffect ? animation.effect.target : null;
          const timing = animation.effect?.getComputedTiming();
          return {
            className: target instanceof Element ? String(target.className) : "",
            iterations: timing?.iterations,
            playState: animation.playState,
          };
        })
        .filter((animation) => animation.playState !== "finished" && animation.iterations === Infinity),
    );
    expect(runningDecorativeAnimations, JSON.stringify(runningDecorativeAnimations, null, 2)).toEqual([]);

    releaseStream?.();
    await expect(page.getByText("报告助手最终回答。")).toBeVisible();
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
