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

  const createRequests: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      request.url().endsWith("/api/ai/conversations")
    ) {
      createRequests.push(request.url());
    }
  });
  const createRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith("/api/ai/conversations"),
  );
  await page.getByPlaceholder(/线程名称/).fill(threadTitle);
  await page.getByRole("button", { name: "新建线程" }).hover();
  await page.getByRole("button", { name: "新建线程" }).dblclick();
  await createRequest;

  await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
  const threadUrl = page.url();
  const threadId = threadUrl.split("/").pop() ?? "";
  await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
  await expect.poll(() => createRequests.length).toBe(1);
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
  const retryRequests: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      request.url().includes(`/api/ai/conversations/${encodeURIComponent(threadId)}/messages`)
    ) {
      retryRequests.push(request.url());
    }
  });
  const retryRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().includes(`/api/ai/conversations/${encodeURIComponent(threadId)}/messages`),
  );
  await retryButton.hover();
  await retryButton.dblclick();
  await retryRequest;
  await expect(page.locator(".ct-codex-message.is-user").filter({ hasText: prompt })).toHaveCount(2);
  await expect.poll(() => retryRequests.length).toBe(1);
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

test("prevents duplicate sibling AI thread creation from a real double click", async ({
  page,
  request,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-sibling-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI sibling thread e2e workspace\n", "utf8");
  const workspaceName = `ai-sibling-e2e-${Date.now()}`;
  const firstThreadTitle = `${workspaceName} primary investigation`;
  const siblingTitle = `${workspaceName} · 新调查`;

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

  await page.getByPlaceholder(/线程名称/).fill(firstThreadTitle);
  await page.getByRole("button", { name: "新建线程" }).hover();
  await page.getByRole("button", { name: "新建线程" }).click();
  await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
  await expect(page.getByRole("heading", { name: firstThreadTitle })).toBeVisible({
    timeout: 15_000,
  });

  const createRequests: string[] = [];
  page.on("request", (req) => {
    if (req.method() === "POST" && req.url().endsWith("/api/ai/conversations")) {
      createRequests.push(req.url());
    }
  });
  const firstSiblingCreate = page.waitForRequest(
    (req) => req.method() === "POST" && req.url().endsWith("/api/ai/conversations"),
  );

  const railNewThread = page.locator(".ct-codex-ai__rail").getByRole("button", { name: "新建线程" });
  await railNewThread.hover();
  await railNewThread.dblclick();
  await firstSiblingCreate;

  await page.waitForURL((url) => /\/ai\/[^/]+$/.test(url.pathname), { timeout: 15_000 });
  await expect(page.getByRole("heading", { name: siblingTitle })).toBeVisible({
    timeout: 15_000,
  });
  await expect.poll(() => createRequests.length).toBe(1);

  const listResp = await request.get(
    `${backendBase}/api/ai/conversations?workspace_id=${workspace.id}&limit=10`,
  );
  expect(listResp.ok()).toBeTruthy();
  const conversations = (await listResp.json()) as { items: Array<{ title: string }> };
  expect(conversations.items.filter((item) => item.title === firstThreadTitle)).toHaveLength(1);
  expect(conversations.items.filter((item) => item.title === siblingTitle)).toHaveLength(1);
});

test("sends quick actions and memory actions through the real AI thread composer", async ({
  page,
  request,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-actions-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI action buttons e2e workspace\n", "utf8");
  const workspaceName = `ai-actions-e2e-${Date.now()}`;
  const threadTitle = `${workspaceName} action prompts`;
  const quickPrompt = "补充黑盒边界条件和异常路径";
  const memoryPrompt = "生成复跑建议";

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
  await page.getByPlaceholder(/线程名称/).fill(threadTitle);
  await page.getByRole("button", { name: "新建线程" }).hover();
  await page.getByRole("button", { name: "新建线程" }).click();

  await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
  const threadId = page.url().split("/").pop() ?? "";
  await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({
    timeout: 15_000,
  });

  const sendRequests: string[] = [];
  page.on("request", (req) => {
    if (
      req.method() === "POST" &&
      req.url().includes(`/api/ai/conversations/${encodeURIComponent(threadId)}/messages`)
    ) {
      sendRequests.push(req.url());
    }
  });
  const composer = page.getByLabel("AI 线程消息");

  const quickRequest = page.waitForRequest(
    (req) =>
      req.method() === "POST" &&
      req.url().includes(`/api/ai/conversations/${encodeURIComponent(threadId)}/messages`),
  );
  await page.getByRole("button", { name: quickPrompt }).hover();
  await page.getByRole("button", { name: quickPrompt }).click();
  await expect(composer).toHaveValue(quickPrompt);
  await composer.focus();
  await page.keyboard.press("Enter");
  await quickRequest;
  await expect(page.locator(".ct-codex-message.is-user").filter({ hasText: quickPrompt })).toHaveCount(1);
  await expect(page.locator('div[role="alert"]').filter({ hasText: "未配置活跃的聊天模型" })).toBeVisible({
    timeout: 20_000,
  });

  const memoryRequest = page.waitForRequest(
    (req) =>
      req.method() === "POST" &&
      req.url().includes(`/api/ai/conversations/${encodeURIComponent(threadId)}/messages`),
  );
  await page.getByRole("button", { name: memoryPrompt }).hover();
  await page.getByRole("button", { name: memoryPrompt }).click();
  await expect(composer).toHaveValue(memoryPrompt);
  await composer.focus();
  await page.keyboard.press("Enter");
  await memoryRequest;
  await expect(page.locator(".ct-codex-message.is-user").filter({ hasText: memoryPrompt })).toHaveCount(1);
  await expect.poll(() => sendRequests.length).toBe(2);

  const messagesResp = await request.get(
    `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
  );
  expect(messagesResp.ok()).toBeTruthy();
  const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
  expect(messageBody.items.filter((item) => item.role === "user" && item.content === quickPrompt)).toHaveLength(1);
  expect(messageBody.items.filter((item) => item.role === "user" && item.content === memoryPrompt)).toHaveLength(1);

  const listResp = await request.get(`${backendBase}/api/ai/conversations?workspace_id=${workspace.id}&limit=10`);
  expect(listResp.ok()).toBeTruthy();
  const conversations = (await listResp.json()) as { items: Array<{ id: string; workspace_id: string }> };
  expect(conversations.items).toEqual(
    expect.arrayContaining([expect.objectContaining({ id: threadId, workspace_id: workspace.id })]),
  );
});

test("cancels a running agent-runtime AI thread through the real UI", async ({
  page,
  request,
}) => {
  test.setTimeout(120_000);
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
    await expect(page.locator(".ct-ai-env-card").filter({ hasText: "执行器" })).toContainText(runtimeName);

    const prompt = "开始一个可以被取消的 Agent runtime 调查";
    const sendRequests: string[] = [];
    page.on("request", (request) => {
      if (
        request.method() === "POST" &&
        request.url().includes(`/api/ai/conversations/${encodeURIComponent(threadId)}/messages`)
      ) {
        sendRequests.push(request.url());
      }
    });
    const sendRequest = page.waitForRequest(
      (request) =>
        request.method() === "POST" &&
        request.url().includes(`/api/ai/conversations/${encodeURIComponent(threadId)}/messages`),
    );
    await page.getByPlaceholder(/像 Codex 一样继续追问/).fill(prompt);
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).dblclick();
    await sendRequest;
    await expect(page.locator(".ct-codex-message.is-user").filter({ hasText: prompt })).toHaveCount(1);
    await expect.poll(() => sendRequests.length).toBe(1);
    await expect(page.getByRole("button", { name: "停止" })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("agent-runtime-first-delta")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByLabel("AI 线程消息")).toBeDisabled();
    await expect(page.getByRole("button", { name: "解释这个测试设计背后的风险判断" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "补充黑盒边界条件和异常路径" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "新建线程" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "导出" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "沉淀到当前项目记忆" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "加入测试设计" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "生成复跑建议" })).toBeDisabled();

    const cancelRequests: string[] = [];
    page.on("request", (request) => {
      if (
        request.method() === "POST" &&
        request.url().includes(`/api/ai/conversations/${encodeURIComponent(threadId)}/cancel`)
      ) {
        cancelRequests.push(request.url());
      }
    });
    const cancelRequest = page.waitForRequest(
      (request) =>
        request.method() === "POST" &&
        request.url().includes(`/api/ai/conversations/${encodeURIComponent(threadId)}/cancel`),
    );
    await page.getByRole("button", { name: "停止" }).hover();
    await page.getByRole("button", { name: "停止" }).dblclick();
    await cancelRequest;
    await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0, { timeout: 15_000 });
    await expect(page.getByText("agent-runtime-after-cancel")).toHaveCount(0);
    await expect.poll(() => cancelRequests.length).toBe(1);

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

test("keeps AI thread navigation locked while an agent run is streaming", async ({
  page,
  request,
}) => {
  test.setTimeout(120_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-nav-lock-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI navigation lock e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-nav-lock-")));
  const runtimeScript = path.join(runtimeDir, "slow_nav_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "import time",
      "sys.stdin.read()",
      "print('agent-nav-lock-first-delta', flush=True)",
      "time.sleep(20)",
      "print('agent-nav-lock-after-navigation-window', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-nav-lock-e2e-${Date.now()}`;
  const runtimeName = `Navigation lock runtime ${Date.now()}`;
  const firstThreadTitle = `${workspaceName} primary stream`;

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
      timeout_seconds: 30,
      enabled: true,
    },
  });
  expect(runtimeResp.status()).toBe(201);
  const runtime = (await runtimeResp.json()) as { id: string };

  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);

  try {
    await page.goto("/ai", { waitUntil: "domcontentloaded" });
    const projectButton = page.locator("button").filter({ hasText: workspaceName }).first();
    await expect(projectButton).toBeVisible({ timeout: 15_000 });
    await projectButton.hover();
    await projectButton.click();
    await expect(page.getByRole("heading", { name: workspaceName })).toBeVisible();

    await page.getByLabel("AI 线程执行器").selectOption({ label: runtimeName });
    await page.getByPlaceholder(/线程名称/).fill(firstThreadTitle);
    await page.getByRole("button", { name: "新建线程" }).hover();
    await page.getByRole("button", { name: "新建线程" }).click();

    await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
    const firstThreadUrl = page.url();
    const firstThreadId = firstThreadUrl.split("/").pop() ?? "";
    await expect(page.getByRole("heading", { name: firstThreadTitle })).toBeVisible({
      timeout: 15_000,
    });

    await page.locator(".ct-codex-ai__rail").getByRole("button", { name: "新建线程" }).hover();
    await page.locator(".ct-codex-ai__rail").getByRole("button", { name: "新建线程" }).click();
    await page.waitForURL((url) => /\/ai\/[^/]+$/.test(url.pathname) && url.toString() !== firstThreadUrl, {
      timeout: 15_000,
    });
    const siblingTitle = `${workspaceName} · 新调查`;
    await expect(page.getByRole("heading", { name: siblingTitle })).toBeVisible({
      timeout: 15_000,
    });

    const firstThreadLink = page.locator(".ct-codex-ai__thread-list").getByRole("link", {
      name: firstThreadTitle,
    });
    await firstThreadLink.hover();
    await firstThreadLink.click();
    await expect(page).toHaveURL(new RegExp(`/ai/${firstThreadId}$`));
    await expect(page.getByRole("heading", { name: firstThreadTitle })).toBeVisible();

    await page.getByPlaceholder(/像 Codex 一样继续追问/).fill("开始一个运行中禁止切换线程的调查");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByRole("button", { name: "停止" })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("agent-nav-lock-first-delta")).toBeVisible({ timeout: 20_000 });

    const siblingThreadLink = page.locator(".ct-codex-ai__thread-list").getByRole("link", {
      name: siblingTitle,
    });
    await expect(siblingThreadLink).toHaveAttribute("aria-disabled", "true");
    await siblingThreadLink.hover();
    const siblingThreadBox = await siblingThreadLink.boundingBox();
    expect(siblingThreadBox).not.toBeNull();
    await page.mouse.click(
      siblingThreadBox!.x + siblingThreadBox!.width / 2,
      siblingThreadBox!.y + siblingThreadBox!.height / 2,
    );
    await expect(page).toHaveURL(new RegExp(`/ai/${firstThreadId}$`));
    await expect(page.getByRole("heading", { name: firstThreadTitle })).toBeVisible();

    await page.getByRole("button", { name: "停止" }).hover();
    await page.getByRole("button", { name: "停止" }).click();
    await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0, { timeout: 15_000 });
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("keeps historical AI thread reading stable while an agent run is streaming", async ({
  page,
  request,
}) => {
  test.setTimeout(120_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-scroll-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI scroll stability e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-scroll-")));
  const runtimeScript = path.join(runtimeDir, "scroll_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "import time",
      "prompt = sys.stdin.read()",
      "if 'LIVE_SCROLL_RUN' in prompt:",
      "    print('STREAM-BEGIN stable-reader', flush=True)",
      "    for i in range(1, 90):",
      "        print(f'STREAM-LINE-{i:02d} user-should-not-be-yanked-to-bottom while reading history', flush=True)",
      "        time.sleep(0.04)",
      "    print('STREAM-END stable-reader', flush=True)",
      "else:",
      "    print('HISTORY-BEGIN stable-reader', flush=True)",
      "    for i in range(1, 95):",
      "        print(f'HISTORY-LINE-{i:02d} earlier evidence and reasoning that remains readable during generation', flush=True)",
      "    print('HISTORY-END stable-reader', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-scroll-e2e-${Date.now()}`;
  const runtimeName = `Scroll stability runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} stable reader`;

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
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);

    const composer = page.getByPlaceholder(/像 Codex 一样继续追问/);
    await composer.fill("SEED_HISTORY_RUN 生成一段足够长的历史分析，供后续流式生成时阅读");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("HISTORY-END stable-reader")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0, { timeout: 15_000 });

    const reader = page.getByLabel("AI 线程对话内容");
    await expect
      .poll(async () => reader.evaluate((element) => element.scrollHeight > element.clientHeight * 2))
      .toBeTruthy();

    await composer.fill("LIVE_SCROLL_RUN 继续生成长回答；我会在生成过程中向上滚动阅读历史");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByRole("button", { name: "停止" })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("STREAM-BEGIN stable-reader")).toBeVisible({ timeout: 20_000 });

    await reader.hover();
    await page.mouse.wheel(0, -2600);
    await expect(page.getByText("HISTORY-LINE-40")).toBeVisible({ timeout: 10_000 });
    const scrollTopWhileReading = await reader.evaluate((element) => element.scrollTop);
    const distanceFromBottomWhileReading = await reader.evaluate(
      (element) => element.scrollHeight - element.scrollTop - element.clientHeight,
    );
    expect(distanceFromBottomWhileReading).toBeGreaterThan(240);

    await expect(page.getByText("STREAM-LINE-35 user-should-not-be-yanked-to-bottom")).toBeAttached({
      timeout: 20_000,
    });
    const scrollTopAfterMoreDeltas = await reader.evaluate((element) => element.scrollTop);
    const distanceFromBottomAfterMoreDeltas = await reader.evaluate(
      (element) => element.scrollHeight - element.scrollTop - element.clientHeight,
    );
    expect(scrollTopAfterMoreDeltas).toBeLessThanOrEqual(scrollTopWhileReading + 96);
    expect(distanceFromBottomAfterMoreDeltas).toBeGreaterThan(240);
    await expect(page.getByRole("button", { name: "跳到最新回复" })).toBeVisible();

    await page.getByRole("button", { name: "跳到最新回复" }).hover();
    await page.getByRole("button", { name: "跳到最新回复" }).click();
    await expect
      .poll(async () =>
        reader.evaluate((element) => element.scrollHeight - element.scrollTop - element.clientHeight),
      )
      .toBeLessThan(120);
    await expect(page.getByText("STREAM-END stable-reader")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0, { timeout: 15_000 });
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("jumps to latest when sending from a detached AI thread reading position", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-send-scroll-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI send-scroll e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-send-scroll-")));
  const runtimeScript = path.join(runtimeDir, "send_scroll_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "import time",
      "prompt = sys.stdin.read()",
      "if 'SEND_FROM_DETACHED_READER' in prompt:",
      "    print('NEW-TURN-BEGIN latest-position-check', flush=True)",
      "    for i in range(1, 16):",
      "        print(f'NEW-TURN-LINE-{i:02d} should be near latest after user sends', flush=True)",
      "        time.sleep(0.02)",
      "    print('NEW-TURN-END latest-position-check', flush=True)",
      "else:",
      "    print('LONG-HISTORY-BEGIN latest-position-check', flush=True)",
      "    for i in range(1, 100):",
      "        print(f'LONG-HISTORY-LINE-{i:02d} retained context before next prompt', flush=True)",
      "    print('LONG-HISTORY-END latest-position-check', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-send-scroll-e2e-${Date.now()}`;
  const runtimeName = `Send scroll runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} send from history`;

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
      timeout_seconds: 30,
      enabled: true,
    },
  });
  expect(runtimeResp.status()).toBe(201);
  const runtime = (await runtimeResp.json()) as { id: string };

  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);

  try {
    await page.goto("/ai", { waitUntil: "domcontentloaded" });
    const projectButton = page.locator("button").filter({ hasText: workspaceName }).first();
    await expect(projectButton).toBeVisible({ timeout: 15_000 });
    await projectButton.hover();
    await projectButton.click();

    await page.getByLabel("AI 线程执行器").selectOption({ label: runtimeName });
    await page.getByPlaceholder(/线程名称/).fill(threadTitle);
    await page.getByRole("button", { name: "新建线程" }).hover();
    await page.getByRole("button", { name: "新建线程" }).click();

    await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);

    const composer = page.getByPlaceholder(/像 Codex 一样继续追问/);
    await composer.fill("SEED_LONG_HISTORY 生成长历史，随后从旧位置继续提问");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("LONG-HISTORY-END latest-position-check")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0, { timeout: 15_000 });

    const reader = page.getByLabel("AI 线程对话内容");
    await expect
      .poll(async () => reader.evaluate((element) => element.scrollHeight > element.clientHeight * 2))
      .toBeTruthy();
    await reader.hover();
    await page.mouse.wheel(0, -2600);
    await expect(page.getByText("LONG-HISTORY-LINE-45")).toBeVisible({ timeout: 10_000 });
    await expect
      .poll(async () =>
        reader.evaluate((element) => element.scrollHeight - element.scrollTop - element.clientHeight),
      )
      .toBeGreaterThan(240);

    await composer.fill("SEND_FROM_DETACHED_READER 发送新问题时应该回到最新回复区域");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.getByText("NEW-TURN-BEGIN latest-position-check")).toBeVisible({ timeout: 20_000 });
    await expect
      .poll(async () =>
        reader.evaluate((element) => element.scrollHeight - element.scrollTop - element.clientHeight),
      )
      .toBeLessThan(120);
    await expect(page.getByRole("button", { name: "跳到最新回复" })).toHaveCount(0);
    await expect(page.getByText("NEW-TURN-END latest-position-check")).toBeVisible({ timeout: 30_000 });
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("keeps real agent thinking diagnostics collapsed and out of the persisted answer", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-diag-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI diagnostic folding e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-diag-")));
  const runtimeScript = path.join(runtimeDir, "diagnostic_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "sys.stdin.read()",
      "print('thinking: reading workspace source evidence from lib/nvmf/connect.c', flush=True)",
      "print('diagnostic: provider emitted chain-of-thought-like internal note', flush=True)",
      "print('FINAL_DIAGNOSTIC_ANSWER: black-box reconnect timeout should observe RPC error, log, and state recovery', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-diagnostic-e2e-${Date.now()}`;
  const runtimeName = `Diagnostic runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} folded diagnostics`;
  const prompt = "DIAGNOSTIC_FOLD_RUN 生成答案，并把思考过程默认折叠";

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
      timeout_seconds: 30,
      enabled: true,
    },
  });
  expect(runtimeResp.status()).toBe(201);
  const runtime = (await runtimeResp.json()) as { id: string };

  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);

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

    await page.getByPlaceholder(/像 Codex 一样继续追问/).fill(prompt);
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.getByText("FINAL_DIAGNOSTIC_ANSWER")).toBeVisible({ timeout: 30_000 });
    const reader = page.getByLabel("AI 线程对话内容");
    await expect(reader).not.toContainText("reading workspace source evidence");
    await expect(reader).not.toContainText("chain-of-thought-like internal note");
    await expect(page.getByText("生成诊断：默认折叠")).toBeVisible();
    await expect(page.getByText("reading workspace source evidence")).toBeHidden();
    await expect(page.getByText("chain-of-thought-like internal note")).toBeHidden();

    await page.getByText("生成诊断：默认折叠").click();
    await expect(page.getByText("reading workspace source evidence")).toBeVisible();
    await expect(page.getByText("chain-of-thought-like internal note")).toBeVisible();
    await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0, { timeout: 15_000 });

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as {
      items: Array<{ role: string; content: string }>;
    };
    const assistantMessages = messageBody.items.filter((item) => item.role === "assistant");
    expect(assistantMessages).toHaveLength(1);
    expect(assistantMessages[0].content).toContain("FINAL_DIAGNOSTIC_ANSWER");
    expect(assistantMessages[0].content).not.toContain("thinking:");
    expect(assistantMessages[0].content).not.toContain("diagnostic:");
    expect(assistantMessages[0].content).not.toContain("chain-of-thought-like internal note");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("cleans real external-agent terminal noise before display, persistence, and export", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-noise-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI terminal noise e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-noise-")));
  const runtimeScript = path.join(runtimeDir, "noisy_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "sys.stdin.read()",
      "sys.stdout.write('\\x1b[32m')",
      "sys.stdout.write('47%\\n12/100\\n')",
      "sys.stdout.buffer.write(bytes([0x80, 0x81, 0x8D, 0x90, 0x9D]) + b'\\n')",
      "sys.stdout.flush()",
      "sys.stdout.write('\\r\\x1b[2K⠋ 12\\r\\x1b[2K⠙ 47\\r\\x1b[2K\\x1b(B')",
      "sys.stdout.flush()",
      "sys.stdout.buffer.write('源码证据：连接失败\\n'.encode('gbk'))",
      "sys.stdout.write('FINAL_NOISE_CLEAN_ANSWER: 已完成源码分析。\\n')",
      "sys.stdout.write('\\x1b[0m')",
      "sys.stdout.flush()",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-noise-e2e-${Date.now()}`;
  const runtimeName = `Noisy external runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} terminal noise`;
  const prompt = "NOISE_CLEAN_RUN 请读取工作区并生成最终答案，不能把终端进度噪声混入回答";

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
      timeout_seconds: 30,
      enabled: true,
    },
  });
  expect(runtimeResp.status()).toBe(201);
  const runtime = (await runtimeResp.json()) as { id: string };

  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);

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

    await page.getByLabel("AI 线程消息").fill(prompt);
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("FINAL_NOISE_CLEAN_ANSWER")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("源码证据：连接失败")).toBeVisible();
    await expect(page.locator("body")).not.toContainText("47%");
    await expect(page.locator("body")).not.toContainText("12/100");
    await expect(page.locator("body")).not.toContainText("(B");
    await expect(page.locator("body")).not.toContainText("⠋");
    await expect(page.locator("body")).not.toContainText("⠙");
    await expect(page.locator("body")).not.toContainText("�");
    await expect(page.locator("body")).not.toContainText("[32m");
    await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0, { timeout: 15_000 });

    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("FINAL_NOISE_CLEAN_ANSWER")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("源码证据：连接失败")).toBeVisible();
    await expect(page.locator("body")).not.toContainText("47%");
    await expect(page.locator("body")).not.toContainText("12/100");
    await expect(page.locator("body")).not.toContainText("(B");
    await expect(page.locator("body")).not.toContainText("�");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as {
      items: Array<{ role: string; content: string }>;
    };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("FINAL_NOISE_CLEAN_ANSWER");
    expect(assistant?.content).toContain("源码证据：连接失败");
    expect(assistant?.content).not.toContain("47%");
    expect(assistant?.content).not.toContain("12/100");
    expect(assistant?.content).not.toContain("(B");
    expect(assistant?.content).not.toContain("�");
    expect(assistant?.content).not.toContain("[32m");

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出" }).hover();
    await page.getByRole("button", { name: "导出" }).click();
    const download = await downloadPromise;
    const exportPath = testInfo.outputPath("real-ai-thread-noise-clean-export.md");
    await download.saveAs(exportPath);
    const exported = fs.readFileSync(exportPath, "utf8");
    expect(exported).toContain("FINAL_NOISE_CLEAN_ANSWER");
    expect(exported).toContain("源码证据：连接失败");
    expect(exported).not.toContain("47%");
    expect(exported).not.toContain("12/100");
    expect(exported).not.toContain("(B");
    expect(exported).not.toContain("�");
    expect(exported).not.toContain("[32m");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("completes an agent-runtime AI thread and exports the persisted answer", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-complete-repo-")));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "nvmf", "ctrlr.c"),
    "int nvmf_ctrlr_connect(void) { return 0; }\n",
    "utf8",
  );
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-complete-")));
  const runtimeScript = path.join(runtimeDir, "complete_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "prompt = sys.stdin.read()",
      "print('SPDK agent completed analysis', flush=True)",
      "print('Evidence: lib/nvmf/ctrlr.c nvmf_ctrlr_connect', flush=True)",
      "print('Flow: connect request -> controller setup -> IO queue ready', flush=True)",
      "print('Prompt echoed:', prompt[:80].replace('\\n', ' '), flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-complete-e2e-${Date.now()}`;
  const runtimeName = `Complete runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} successful agent run`;
  const prompt = "分析 SPDK NVMe-oF target connect 到 IO 提交流程，并列出关键文件证据";

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
      timeout_seconds: 30,
      enabled: true,
    },
  });
  expect(runtimeResp.status()).toBe(201);
  const runtime = (await runtimeResp.json()) as { id: string };

  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);
  const workspace = (await workspaceResp.json()) as { id: string };

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

    await page.getByPlaceholder(/像 Codex 一样继续追问/).fill(prompt);
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.locator(".ct-codex-message.is-user").filter({ hasText: prompt })).toHaveCount(1);
    await expect(page.getByText("SPDK agent completed analysis")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Evidence: lib/nvmf/ctrlr.c nvmf_ctrlr_connect")).toBeVisible();
    await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0, { timeout: 15_000 });

    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText("SPDK agent completed analysis")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("Evidence: lib/nvmf/ctrlr.c nvmf_ctrlr_connect")).toBeVisible();

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出" }).hover();
    await page.getByRole("button", { name: "导出" }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(new RegExp(`${workspaceName}.*\\.md$`));
    const exportPath = testInfo.outputPath("real-ai-thread-success-export.md");
    await download.saveAs(exportPath);
    const exported = fs.readFileSync(exportPath, "utf8");
    expect(exported).toContain(`# ${threadTitle}`);
    expect(exported).toContain(prompt);
    expect(exported).toContain("SPDK agent completed analysis");
    expect(exported).toContain("Evidence: lib/nvmf/ctrlr.c nvmf_ctrlr_connect");
    expect(exported).not.toMatch(/sk-[A-Za-z0-9_-]{12,}/);
    expect(exported).not.toMatch(/Authorization:\s*Bearer\s+[^\s"']+/i);
    expect(exported).not.toMatch(/(?:api[-_]?key|token|secret|password)=['"]?[^\s"']+/i);

    const conversationResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}`,
    );
    expect(conversationResp.ok()).toBeTruthy();
    const conversation = (await conversationResp.json()) as {
      status: string;
      latest_run: { status: string; model: string | null } | null;
      workspace_id: string;
    };
    expect(conversation.status).toBe("idle");
    expect(conversation.latest_run?.status).toBe("completed");
    expect(conversation.workspace_id).toBe(workspace.id);

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as {
      items: Array<{ role: string; content: string }>;
    };
    expect(messageBody.items.filter((item) => item.role === "user" && item.content === prompt)).toHaveLength(1);
    expect(
      messageBody.items.some(
        (item) => item.role === "assistant" && item.content.includes("SPDK agent completed analysis"),
      ),
    ).toBeTruthy();
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("injects requested workspace source into a real agent-runtime AI thread", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-source-repo-")));
  const sourcePath = path.join(repo, "lib", "nvmf", "connect.c");
  fs.mkdirSync(path.dirname(sourcePath), { recursive: true });
  fs.writeFileSync(
    sourcePath,
    [
      "int spdk_nvmf_source_injection_probe(void) {",
      "    return 20260701;",
      "}",
      "",
    ].join("\n"),
    "utf8",
  );
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-source-")));
  const runtimeScript = path.join(runtimeDir, "source_asserting_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "prompt = sys.stdin.read()",
      "required = [",
      "    'workspace_source',",
      "    'lib/nvmf/connect.c',",
      "    'spdk_nvmf_source_injection_probe',",
      "    'return 20260701;',",
      "]",
      "missing = [item for item in required if item not in prompt]",
      "if missing:",
      "    print('SOURCE_CONTEXT_MISSING ' + ','.join(missing), flush=True)",
      "else:",
      "    print('SOURCE_CONTEXT_OK lib/nvmf/connect.c spdk_nvmf_source_injection_probe', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-source-e2e-${Date.now()}`;
  const runtimeName = `Source asserting runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} source injection`;
  const prompt = "请读取 lib/nvmf/connect.c 并基于 spdk_nvmf_source_injection_probe 分析 connect 流程";

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
      timeout_seconds: 30,
      enabled: true,
    },
  });
  expect(runtimeResp.status()).toBe(201);
  const runtime = (await runtimeResp.json()) as { id: string };

  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);
  const workspace = (await workspaceResp.json()) as { id: string };

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

    await page.getByLabel("AI 线程消息").fill(prompt);
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.locator(".ct-codex-message.is-user").filter({ hasText: prompt })).toHaveCount(1);
    await expect(page.getByText("SOURCE_CONTEXT_OK lib/nvmf/connect.c")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("SOURCE_CONTEXT_MISSING")).toHaveCount(0);
    await expect(page.getByText("源码位置")).toBeVisible();
    await expect(page.getByText("lib/nvmf/connect.c:L1")).toBeVisible();

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const body = (await messagesResp.json()) as {
      items: Array<{
        role: string;
        content: string;
        references?: Array<{ source_type: string; metadata?: Record<string, unknown> }>;
      }>;
    };
    const userMessage = body.items.find((item) => item.role === "user" && item.content === prompt);
    expect(userMessage?.references).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          source_type: "workspace_source",
          metadata: expect.objectContaining({
            workspace_id: workspace.id,
            path: "lib/nvmf/connect.c",
          }),
        }),
      ]),
    );
    expect(JSON.stringify(userMessage?.references ?? [])).not.toContain(repo);
    expect(
      body.items.some(
        (item) => item.role === "assistant" && item.content.includes("SOURCE_CONTEXT_OK lib/nvmf/connect.c"),
      ),
    ).toBeTruthy();

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出" }).hover();
    await page.getByRole("button", { name: "导出" }).click();
    const download = await downloadPromise;
    const exportPath = test.info().outputPath("real-ai-thread-source-public-path-export.md");
    await download.saveAs(exportPath);
    const exported = fs.readFileSync(exportPath, "utf8");
    expect(exported).toContain("SOURCE_CONTEXT_OK lib/nvmf/connect.c");
    expect(exported).toContain("源码位置: lib/nvmf/connect.c:L1");
    expect(exported).not.toContain(repo);
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("injects default workspace source into an agent-runtime AI thread for vague prompts", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-default-source-")));
  const sourcePath = path.join(repo, "src", "entry.c");
  fs.mkdirSync(path.dirname(sourcePath), { recursive: true });
  fs.writeFileSync(path.join(repo, "README.md"), "默认源码注入验证工作区\n", "utf8");
  fs.writeFileSync(
    sourcePath,
    [
      "int codetalk_default_workspace_source_probe(void) {",
      "    return 314159;",
      "}",
      "",
    ].join("\n"),
    "utf8",
  );
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-default-source-")));
  const runtimeScript = path.join(runtimeDir, "default_source_asserting_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "prompt = sys.stdin.read()",
      "required = [",
      "    'workspace_source',",
      "    'src/entry.c',",
      "    'codetalk_default_workspace_source_probe',",
      "    'return 314159;',",
      "]",
      "missing = [item for item in required if item not in prompt]",
      "if missing:",
      "    print('DEFAULT_SOURCE_CONTEXT_MISSING ' + ','.join(missing), flush=True)",
      "else:",
      "    print('DEFAULT_SOURCE_CONTEXT_OK src/entry.c codetalk_default_workspace_source_probe', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-default-source-${Date.now()}`;
  const runtimeName = `Default source runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} vague source`;
  const prompt = "分析这个工作区的主流程，优先依据本地源码，不要只凭模型记忆";

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
      timeout_seconds: 30,
      enabled: true,
    },
  });
  expect(runtimeResp.status()).toBe(201);
  const runtime = (await runtimeResp.json()) as { id: string };

  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);
  const workspace = (await workspaceResp.json()) as { id: string };

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
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);

    await page.getByLabel("AI 线程消息").fill(prompt);
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.locator(".ct-codex-message.is-user").filter({ hasText: prompt })).toHaveCount(1);
    await expect(page.getByText("DEFAULT_SOURCE_CONTEXT_OK src/entry.c")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("DEFAULT_SOURCE_CONTEXT_MISSING")).toHaveCount(0);
    await expect(page.getByText("src/entry.c:L1")).toBeVisible();

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const body = (await messagesResp.json()) as {
      items: Array<{
        role: string;
        content: string;
        references?: Array<{ source_type: string; metadata?: Record<string, unknown> }>;
      }>;
    };
    const userMessage = body.items.find((item) => item.role === "user" && item.content === prompt);
    expect(userMessage?.references).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          source_type: "workspace_source",
          metadata: expect.objectContaining({
            workspace_id: workspace.id,
            path: "src/entry.c",
          }),
        }),
      ]),
    );
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("redacts persisted AI thread message secrets from exported markdown", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-redact-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI export redaction e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-redact-")));
  const runtimeScript = path.join(runtimeDir, "redact_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "sys.stdin.read()",
      "print('AI export redaction probe complete', flush=True)",
      "print('agent key: ' + 'sk' + '-' + 'aiThreadExportLeakValue1234567890', flush=True)",
      "print('runtime ' + 'tok' + 'en=' + 'aiThreadTokenLeakValue1234567890', flush=True)",
      "print('Authorization: Bearer ' + 'aiThreadBearerLeakValue1234567890', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-redact-e2e-${Date.now()}`;
  const runtimeName = `Redaction runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} export redaction`;
  const userSecret = ["sk", "userThreadExportLeakValue1234567890"].join("-");
  const runtimeSecret = ["sk", "aiThreadExportLeakValue1234567890"].join("-");
  const tokenSecret = "aiThreadTokenLeakValue1234567890";
  const bearerSecret = "aiThreadBearerLeakValue1234567890";
  const prompt = `请分析导出脱敏，并确认不要泄露 ${userSecret}`;

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
      timeout_seconds: 30,
      enabled: true,
    },
  });
  expect(runtimeResp.status()).toBe(201);
  const runtime = (await runtimeResp.json()) as { id: string };

  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);

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
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);

    await page.getByPlaceholder(/像 Codex 一样继续追问/).fill(prompt);
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.locator(".ct-codex-message.is-user").filter({ hasText: "请分析导出脱敏" })).toHaveCount(1);
    await expect(page.getByText("AI export redaction probe complete")).toBeVisible({ timeout: 30_000 });
    await expect(page.locator("body")).toContainText("<redacted>");
    await expect(page.locator("body")).not.toContainText(userSecret);
    await expect(page.locator("body")).not.toContainText(runtimeSecret);
    await expect(page.locator("body")).not.toContainText(tokenSecret);
    await expect(page.locator("body")).not.toContainText(bearerSecret);

    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("AI export redaction probe complete")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator("body")).toContainText("<redacted>");
    await expect(page.locator("body")).not.toContainText(userSecret);
    await expect(page.locator("body")).not.toContainText(runtimeSecret);
    await expect(page.locator("body")).not.toContainText(tokenSecret);
    await expect(page.locator("body")).not.toContainText(bearerSecret);

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出" }).hover();
    await page.getByRole("button", { name: "导出" }).click();
    const download = await downloadPromise;
    const exportPath = testInfo.outputPath("real-ai-thread-redacted-export.md");
    await download.saveAs(exportPath);
    const exported = fs.readFileSync(exportPath, "utf8");
    expect(exported).toContain(`# ${threadTitle}`);
    expect(exported).toContain("AI export redaction probe complete");
    expect(exported).toContain("<redacted>");
    expect(exported).not.toContain(userSecret);
    expect(exported).not.toContain(runtimeSecret);
    expect(exported).not.toContain(tokenSecret);
    expect(exported).not.toContain(bearerSecret);
    expect(exported).not.toMatch(/sk-[A-Za-z0-9_-]{12,}/);
    expect(exported).not.toMatch(/Authorization:\s*Bearer\s+(?!<redacted>)[^\s"']+/i);
    expect(exported).not.toMatch(/(?:api[-_]?key|token|secret|password)=['"]?(?!<redacted>)[^\s"']+/i);
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("sends an AI thread message with Enter while Shift+Enter keeps a newline", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-keyboard-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI keyboard e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-keyboard-")));
  const runtimeScript = path.join(runtimeDir, "keyboard_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "prompt = sys.stdin.read()",
      "print('KEYBOARD_AGENT_REPLY')",
      "print('has_multiline_prompt=' + str('第一行：分析 SPDK reconnect\\n第二行：保留上下文再发送' in prompt).lower())",
      "print('user_line_occurrences=' + str(prompt.count('第一行：分析 SPDK reconnect')) + '/' + str(prompt.count('第二行：保留上下文再发送')))",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-keyboard-e2e-${Date.now()}`;
  const runtimeName = `Keyboard runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} keyboard send`;
  const firstLine = "第一行：分析 SPDK reconnect";
  const secondLine = "第二行：保留上下文再发送";
  const prompt = `${firstLine}\n${secondLine}`;

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
      timeout_seconds: 30,
      enabled: true,
    },
  });
  expect(runtimeResp.status()).toBe(201);
  const runtime = (await runtimeResp.json()) as { id: string };

  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);

  try {
    await page.goto("/ai", { waitUntil: "domcontentloaded" });
    const projectButton = page.locator("button").filter({ hasText: workspaceName }).first();
    await expect(projectButton).toBeVisible({ timeout: 15_000 });
    await projectButton.hover();
    await projectButton.click();

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

    const composer = page.getByLabel("AI 线程消息");
    await composer.fill(firstLine);
    await page.keyboard.press("Shift+Enter");
    await composer.pressSequentially(secondLine);
    await expect(composer).toHaveValue(prompt);

    await page.keyboard.press("Enter");
    await expect(page.locator(".ct-codex-message.is-user").filter({ hasText: firstLine })).toHaveCount(1);
    await expect(page.locator(".ct-codex-message.is-user").filter({ hasText: secondLine })).toHaveCount(1);
    await expect(page.getByText("KEYBOARD_AGENT_REPLY")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("has_multiline_prompt=true")).toBeVisible();
    await expect(page.getByText(/user_line_occurrences=[1-9]\d*\/[1-9]\d*/)).toBeVisible();
    await expect(composer).toHaveValue("");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as {
      items: Array<{ role: string; content: string }>;
    };
    expect(messageBody.items.filter((item) => item.role === "user" && item.content === prompt)).toHaveLength(1);
    expect(
      messageBody.items.some((item) => item.role === "assistant" && item.content.includes("KEYBOARD_AGENT_REPLY")),
    ).toBeTruthy();
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("switches an idle AI thread executor through the real UI and persists it", async ({
  page,
  request,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-runtime-switch-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI runtime switch e2e workspace\n", "utf8");
  const workspaceName = `ai-runtime-switch-${Date.now()}`;
  const runtimeName = `Runtime switch ${Date.now()}`;
  const threadTitle = `${workspaceName} runtime picker`;

  const runtimeResp = await request.post(`${backendBase}/api/settings/agent-runtimes`, {
    data: {
      name: runtimeName,
      command: "python3",
      args: ["--version"],
      prompt_transport: "stdin",
      output_mode: "plain",
      working_dir_mode: "project",
      fixed_working_dir: "",
      env: {},
      health_command: "",
      timeout_seconds: 30,
      enabled: true,
    },
  });
  expect(runtimeResp.status()).toBe(201);
  const runtime = (await runtimeResp.json()) as { id: string };

  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);
  const workspace = (await workspaceResp.json()) as { id: string };

  try {
    await page.goto("/ai", { waitUntil: "domcontentloaded" });
    const projectButton = page.locator("button").filter({ hasText: workspaceName }).first();
    await expect(projectButton).toBeVisible({ timeout: 15_000 });
    await projectButton.hover();
    await projectButton.click();
    await expect(page.getByRole("heading", { name: workspaceName })).toBeVisible();

    await page.getByLabel("AI 线程执行器").selectOption("builtin_llm");
    await page.getByPlaceholder(/线程名称/).fill(threadTitle);
    await page.getByRole("button", { name: "新建线程" }).hover();
    await page.getByRole("button", { name: "新建线程" }).click();

    await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
    const threadId = page.url().split("/").pop() ?? "";
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({
      timeout: 15_000,
    });
    const threadRuntimeSelect = page.getByLabel("当前 AI 执行器");
    await expect(threadRuntimeSelect).toHaveValue("builtin_llm");
    await expect(page.locator(".ct-ai-env-card").filter({ hasText: "执行器" })).toContainText("内置模型");

    await threadRuntimeSelect.hover();
    await threadRuntimeSelect.selectOption(runtime.id);
    await expect(threadRuntimeSelect).toHaveValue(runtime.id);
    await expect(page.locator(".ct-ai-env-card").filter({ hasText: "执行器" })).toContainText(runtimeName);

    const switchedResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}`,
    );
    expect(switchedResp.ok()).toBeTruthy();
    const switched = (await switchedResp.json()) as {
      runtime_type: string;
      agent_runtime_id: string | null;
      workspace_id: string;
    };
    expect(switched.runtime_type).toBe("agent_runtime");
    expect(switched.agent_runtime_id).toBe(runtime.id);
    expect(switched.workspace_id).toBe(workspace.id);

    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({
      timeout: 15_000,
    });
    await expect(threadRuntimeSelect).toHaveValue(runtime.id);
    await expect(page.locator(".ct-ai-env-card").filter({ hasText: "执行器" })).toContainText(runtimeName);

    await threadRuntimeSelect.hover();
    await threadRuntimeSelect.selectOption("builtin_llm");
    await expect(threadRuntimeSelect).toHaveValue("builtin_llm");
    await expect(page.locator(".ct-ai-env-card").filter({ hasText: "执行器" })).toContainText("内置模型");

    const restoredResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}`,
    );
    expect(restoredResp.ok()).toBeTruthy();
    const restored = (await restoredResp.json()) as {
      runtime_type: string;
      agent_runtime_id: string | null;
    };
    expect(restored.runtime_type).toBe("builtin_llm");
    expect(restored.agent_runtime_id).toBeNull();
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("creates a sibling AI thread from the existing thread sidebar through the real UI", async ({
  page,
  request,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-sibling-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI sibling thread e2e workspace\n", "utf8");
  const workspaceName = `ai-sibling-e2e-${Date.now()}`;
  const firstThreadTitle = `${workspaceName} first investigation`;

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

  await page.getByPlaceholder(/线程名称/).fill(firstThreadTitle);
  await page.getByRole("button", { name: "新建线程" }).hover();
  await page.getByRole("button", { name: "新建线程" }).click();

  await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
  const firstThreadUrl = page.url();
  const firstThreadId = firstThreadUrl.split("/").pop() ?? "";
  await expect(page.getByRole("heading", { name: firstThreadTitle })).toBeVisible({
    timeout: 15_000,
  });

  const sidebarNewThread = page.locator(".ct-codex-ai__rail").getByRole("button", {
    name: "新建线程",
  });
  await sidebarNewThread.hover();
  await sidebarNewThread.click();
  await page.waitForURL((url) => /\/ai\/[^/]+$/.test(url.pathname) && url.toString() !== firstThreadUrl, {
    timeout: 15_000,
  });
  const siblingThreadUrl = page.url();
  const siblingThreadId = siblingThreadUrl.split("/").pop() ?? "";
  expect(siblingThreadId).not.toEqual(firstThreadId);
  await expect(page.getByRole("heading", { name: `${workspaceName} · 新调查` })).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByText(`workspace / ${workspace.id}`)).toBeVisible();
  await expect(page.getByText(`workspace:${workspace.id}`)).toBeVisible();
  await expect(page.locator(".ct-codex-ai__thread-list").getByText(firstThreadTitle)).toBeVisible();
  await expect(page.locator(".ct-codex-ai__thread-list").getByText(`${workspaceName} · 新调查`)).toBeVisible();

  const listResp = await request.get(`${backendBase}/api/ai/conversations?workspace_id=${workspace.id}&limit=10`);
  expect(listResp.ok()).toBeTruthy();
  const conversations = (await listResp.json()) as {
    items: Array<{
      id: string;
      title: string;
      scope_type: string;
      scope_id: string;
      workspace_id: string;
      memory_namespace: string;
    }>;
  };
  expect(conversations.items).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        id: firstThreadId,
        title: firstThreadTitle,
        scope_type: "workspace",
        scope_id: workspace.id,
        workspace_id: workspace.id,
        memory_namespace: `workspace:${workspace.id}`,
      }),
      expect.objectContaining({
        id: siblingThreadId,
        title: `${workspaceName} · 新调查`,
        scope_type: "workspace",
        scope_id: workspace.id,
        workspace_id: workspace.id,
        memory_namespace: `workspace:${workspace.id}`,
      }),
    ]),
  );
});

test("collapses and restores the AI thread context panel through the real UI", async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk_ai_context_panel_")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI context panel e2e workspace\n", "utf8");
  const workspaceName = `ai_context_panel_${Date.now()}`;
  const threadTitle = `${workspaceName} layout probe`;

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

  await page.getByPlaceholder(/线程名称/).fill(threadTitle);
  await page.getByRole("button", { name: "新建线程" }).hover();
  await page.getByRole("button", { name: "新建线程" }).click();

  await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
  await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByText(`workspace:${workspace.id}`)).toBeVisible();

  const shell = page.locator(".ct-codex-ai");
  const contextPanel = page.locator(".ct-codex-ai__context");
  await expect(shell).toHaveClass(/is-context-open/);
  await expect(contextPanel).toBeVisible();
  const openWidth = await contextPanel.evaluate((node) => node.getBoundingClientRect().width);
  expect(openWidth).toBeGreaterThan(240);

  await page.locator(".ct-codex-ai__context-toggle").hover();
  await page.locator(".ct-codex-ai__context-toggle").click();
  await expect(shell).not.toHaveClass(/is-context-open/);
  await expect
    .poll(() => contextPanel.evaluate((node) => node.getBoundingClientRect().width))
    .toBeLessThan(Math.min(60, openWidth / 4));
  await expect(page.getByLabel("AI 线程消息")).toBeVisible();

  await page.getByRole("button", { name: "环境" }).hover();
  await page.getByRole("button", { name: "环境" }).click();
  await expect(shell).toHaveClass(/is-context-open/);
  await expect
    .poll(() => contextPanel.evaluate((node) => node.getBoundingClientRect().width))
    .toBeGreaterThan(240);
  await expect(page.getByText(`workspace:${workspace.id}`)).toBeVisible();
});
