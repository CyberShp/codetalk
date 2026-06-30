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
    await expect(page.getByLabel("AI 线程消息")).toBeDisabled();
    await expect(page.getByRole("button", { name: "解释这个测试设计背后的风险判断" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "补充黑盒边界条件和异常路径" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "新建线程" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "导出" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "沉淀到当前项目记忆" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "加入测试设计" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "生成复跑建议" })).toBeDisabled();

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
      "print('lines=' + str(prompt.count('\\n') + 1))",
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
    await expect(page.getByText("lines=2")).toBeVisible();
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
