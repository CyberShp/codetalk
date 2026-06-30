import { expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

test("creates an AI investigation thread from the project hub and restores it after refresh", async ({
  page,
  request,
}, testInfo) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-thread-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI thread real e2e workspace\n", "utf8");
  const workspaceName = `ai-thread-e2e-${Date.now()}`;
  const threadTitle = `${workspaceName} NVMe-oF connect 调查`;

  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);
  const workspace = (await workspaceResp.json()) as { id: string };

  await page.goto("/ai", { waitUntil: "domcontentloaded" });
  const projectButton = page.locator("button").filter({ hasText: workspaceName }).first();
  await expect(projectButton).toBeVisible({ timeout: 15_000 });
  await projectButton.hover();
  await projectButton.click();

  await expect(page.getByRole("heading", { name: workspaceName })).toBeVisible();
  await expect(page.getByText("这个项目还没有 AI 调查线程")).toBeVisible();

  await page.getByPlaceholder(/线程名称/).fill(threadTitle);
  await page.getByRole("button", { name: "新建线程" }).hover();
  await page.getByRole("button", { name: "新建线程" }).click();

  await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
  const threadUrl = page.url();
  const threadId = threadUrl.split("/").pop() ?? "";
  await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("直接提问。这个线程会持续保存")).toBeVisible();
  const composer = page.getByPlaceholder(/像 Codex 一样继续追问/);
  await expect(composer).toBeVisible();

  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("直接提问。这个线程会持续保存")).toBeVisible();

  const prompt = "分析 SPDK NVMe-oF target connect 到 IO 提交流程";
  await composer.fill(prompt);
  await page.getByRole("button", { name: "发送" }).hover();
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.locator(".ct-codex-message.is-user").filter({ hasText: prompt })).toHaveCount(1);

  const alert = page.locator('div[role="alert"]').filter({ hasText: "未配置活跃的聊天模型" });
  await expect(alert).toBeVisible({ timeout: 20_000 });
  await expect(alert).toContainText("LLM 不可用");
  await expect(page.getByRole("link", { name: "去设置执行器" })).toHaveAttribute("href", "/settings");
  const retryButton = page.getByRole("button", { name: "重试上一条" });
  await expect(retryButton).toBeVisible();
  await retryButton.hover();
  await retryButton.click();
  await expect(page.locator(".ct-codex-message.is-user").filter({ hasText: prompt })).toHaveCount(2);
  await expect(alert).toBeVisible({ timeout: 20_000 });

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出" }).hover();
  await page.getByRole("button", { name: "导出" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(new RegExp(`${workspaceName}.*\\.md$`));
  const exportPath = testInfo.outputPath("real-ai-thread-failure-export.md");
  await download.saveAs(exportPath);
  const exported = fs.readFileSync(exportPath, "utf8");
  expect(exported).toContain(`# ${threadTitle}`);
  expect(exported).toContain("## 最近失败");
  expect(exported).toContain("LLM 不可用");
  expect(exported).toContain("未配置活跃的聊天模型");
  expect(exported).toContain(prompt);
  expect(exported.match(/## 用户/g)?.length).toBe(2);
  expect(exported).not.toMatch(/sk-[A-Za-z0-9_-]{12,}/);
  expect(exported).not.toMatch(/Authorization:\s*Bearer\s+[^\s"']+/i);
  expect(exported).not.toMatch(/(?:api[-_]?key|token|secret|password)=['"]?[^\s"']+/i);

  await page.goto("/ai", { waitUntil: "domcontentloaded" });
  await projectButton.hover();
  await projectButton.click();
  const threadCard = page.getByRole("link", { name: new RegExp(threadTitle) });
  await expect(threadCard).toBeVisible({ timeout: 15_000 });
  await threadCard.hover();
  await threadCard.click();
  await expect(page).toHaveURL(threadUrl);

  const listResp = await request.get(`${backendBase}/api/ai/conversations?workspace_id=${workspace.id}&limit=10`);
  expect(listResp.ok()).toBeTruthy();
  const conversations = (await listResp.json()) as { items: Array<{ title: string; workspace_id: string }> };
  expect(conversations.items).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ title: threadTitle, workspace_id: workspace.id }),
    ]),
  );

  const messagesResp = await request.get(
    `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
  );
  expect(messagesResp.ok()).toBeTruthy();
  const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
  expect(messageBody.items.filter((item) => item.role === "user" && item.content === prompt)).toHaveLength(2);
  expect(messageBody.items.filter((item) => item.role === "assistant")).toHaveLength(0);
});

test("cancels a running agent-runtime AI thread through the real UI", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-cancel-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI cancel runtime e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-runtime-")));
  const runtimeScript = path.join(runtimeDir, "slow_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "import time",
      "sys.stdin.read()",
      "print('agent-runtime-first-delta', flush=True)",
      "time.sleep(20)",
      "print('agent-runtime-after-cancel', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-cancel-e2e-${Date.now()}`;
  const runtimeName = `Slow cancel runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} runtime cancel`;

  const runtimeResp = await request.post(`${backendBase}/api/settings/agent-runtimes`, {
    data: {
      name: runtimeName,
      command: "python3",
      args: [runtimeScript],
      prompt_transport: "stdin",
      output_mode: "plain",
      working_dir_mode: "project",
      fixed_working_dir: "",
      env: {},
      health_command: "",
      timeout_seconds: 60,
      enabled: true,
    },
  });
  expect(runtimeResp.status()).toBe(201);
  const runtime = (await runtimeResp.json()) as { id: string };

  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);
  await workspaceResp.json();

  try {
    await page.goto("/ai", { waitUntil: "domcontentloaded" });
    const projectButton = page.locator("button").filter({ hasText: workspaceName }).first();
    await expect(projectButton).toBeVisible({ timeout: 15_000 });
    await projectButton.hover();
    await projectButton.click();
    await expect(page.getByRole("heading", { name: workspaceName })).toBeVisible();

    await page.getByLabel("AI 线程执行器").selectOption({ label: runtimeName });
    await page.getByPlaceholder(/线程名称/).fill(threadTitle);
    await page.getByRole("button", { name: "新建线程" }).hover();
    await page.getByRole("button", { name: "新建线程" }).click();

    await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
    const threadId = page.url().split("/").pop() ?? "";
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);
    await expect(page.locator("strong").filter({ hasText: runtimeName })).toBeVisible();

    const prompt = "开始一个可以被取消的 Agent runtime 调查";
    await page.getByPlaceholder(/像 Codex 一样继续追问/).fill(prompt);
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.locator(".ct-codex-message.is-user").filter({ hasText: prompt })).toHaveCount(1);
    await expect(page.getByRole("button", { name: "停止" })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("agent-runtime-first-delta")).toBeVisible({ timeout: 20_000 });

    await page.getByRole("button", { name: "停止" }).hover();
    await page.getByRole("button", { name: "停止" }).click();
    await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0, { timeout: 15_000 });
    await expect(page.getByText("agent-runtime-after-cancel")).toHaveCount(0);

    const conversationResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}`,
    );
    expect(conversationResp.ok()).toBeTruthy();
    const conversation = (await conversationResp.json()) as {
      status: string;
      latest_run: { status: string; model: string | null } | null;
    };
    expect(conversation.status).toBe("idle");
    expect(conversation.latest_run?.status).toBe("cancelled");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as {
      items: Array<{ role: string; content: string }>;
    };
    expect(messageBody.items.filter((item) => item.role === "user" && item.content === prompt)).toHaveLength(1);
    expect(messageBody.items.filter((item) => item.role === "assistant")).toHaveLength(0);
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});
