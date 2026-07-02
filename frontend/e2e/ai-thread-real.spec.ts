import { expect, test, type APIRequestContext } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

async function createDeterministicFailingRuntime(
  request: APIRequestContext,
  label: string,
): Promise<{ id: string; name: string }> {
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-failure-")));
  const runtimeScript = path.join(runtimeDir, "failing_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "sys.stdin.read()",
      "sys.stderr.write('deterministic AI thread failure\\n')",
      "sys.stderr.flush()",
      "raise SystemExit(7)",
      "",
    ].join("\n"),
    "utf8",
  );
  const runtimeName = `${label} ${Date.now()}`;
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
      timeout_seconds: 10,
      enabled: true,
    },
  });
  expect(runtimeResp.status()).toBe(201);
  const runtime = (await runtimeResp.json()) as { id: string };
  return { id: runtime.id, name: runtimeName };
}

async function createClaudeToolResultBlockRuntime(
  request: APIRequestContext,
  label: string,
): Promise<{ id: string; name: string }> {
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-claude-block-")));
  const runtimeScript = path.join(runtimeDir, "claude_tool_result_block_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, os, sys, time",
      "prompt_file = os.environ.get('CODETALK_AGENT_PROMPT_FILE')",
      "if prompt_file:",
      "    open(prompt_file, encoding='utf-8').read()",
      "answer = '## 黑盒测试用例\\n' + ''.join([f'{index}. TC-{index:02d} 正常登录变体：前置条件 target 已启动，步骤执行 iSCSI Login 场景 {index}，预期结果进入 Full Feature Phase 或返回明确 Login Response。\\n' for index in range(1, 9)])",
      "events = [",
      "  {'type':'system','subtype':'init','session_id':'claude-session-e2e'},",
      "  {'type':'stream_event','event':{'type':'content_block_start','index':0,'content_block':{'type':'tool_result','tool_use_id':'toolu_1'}}},",
      "  {'type':'stream_event','event':{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'1115:iscsi_conn_login_pdu_success_complete(void *arg)\\n'}}},",
      "  {'type':'stream_event','event':{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'lib/iscsi/iscsi.c:1539:\\tAuthMethod=CHAP\\n'}}},",
      "  {'type':'stream_event','event':{'type':'content_block_stop','index':0}},",
      "  {'type':'stream_event','event':{'type':'content_block_start','index':1,'content_block':{'type':'text'}}},",
      "  {'type':'stream_event','event':{'type':'content_block_delta','index':1,'delta':{'type':'text_delta','text':answer}}},",
      "  {'type':'stream_event','event':{'type':'content_block_stop','index':1}},",
      "  {'type':'result','status':'success','session_id':'claude-session-e2e'},",
      "]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "    time.sleep(0.05)",
      "",
    ].join("\n"),
    "utf8",
  );
  const runtimeName = `${label} ${Date.now()}`;
  const runtimeResp = await request.post(`${backendBase}/api/settings/agent-runtimes`, {
    data: {
      name: runtimeName,
      command: "python3",
      args: [runtimeScript],
      prompt_transport: "claude_print_arg",
      output_mode: "stream_json",
      working_dir_mode: "project",
      fixed_working_dir: "",
      env: {},
      health_command: "",
      timeout_seconds: 20,
      enabled: true,
      completion_mode: "process_exit",
      session_persistence: "none",
    },
  });
  expect(runtimeResp.status()).toBe(201);
  const runtime = (await runtimeResp.json()) as { id: string };
  return { id: runtime.id, name: runtimeName };
}

async function createClaudeResultFinalRuntime(
  request: APIRequestContext,
  label: string,
): Promise<{ id: string; name: string }> {
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-claude-result-")));
  const runtimeScript = path.join(runtimeDir, "claude_result_final_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, os, sys, time",
      "prompt_file = os.environ.get('CODETALK_AGENT_PROMPT_FILE')",
      "if prompt_file:",
      "    open(prompt_file, encoding='utf-8').read()",
      "answer = '## 黑盒测试用例\\n' + ''.join([f'{index}. TC-{index:02d} Result 登录场景：前置条件 target 已启动，步骤执行 iSCSI Login 场景 {index}，预期结果可观测。\\n' for index in range(1, 9)])",
      "events = [",
      "  {'type':'system','subtype':'init','session_id':'claude-result-session-e2e'},",
      "  {'type':'assistant','message':{'content':[{'type':'tool_use','name':'Bash','input':{'command':'grep -n \"login\" lib/iscsi/iscsi.c'}}]}},",
      "  {'type':'stream_event','event':{'type':'content_block_start','index':0,'content_block':{'type':'tool_result','tool_use_id':'toolu_1'}}},",
      "  {'type':'stream_event','event':{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'1115:iscsi_conn_login_pdu_success_complete(void *arg)\\n'}}},",
      "  {'type':'stream_event','event':{'type':'content_block_stop','index':0}},",
      "  {'type':'result','subtype':'success','status':'success','session_id':'claude-result-session-e2e','result':answer},",
      "]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "    time.sleep(0.05)",
      "",
    ].join("\n"),
    "utf8",
  );
  const runtimeName = `${label} ${Date.now()}`;
  const runtimeResp = await request.post(`${backendBase}/api/settings/agent-runtimes`, {
    data: {
      name: runtimeName,
      command: "python3",
      args: [runtimeScript],
      prompt_transport: "claude_print_arg",
      output_mode: "stream_json",
      working_dir_mode: "project",
      fixed_working_dir: "",
      env: {},
      health_command: "",
      timeout_seconds: 20,
      enabled: true,
      completion_mode: "process_exit",
      session_persistence: "none",
    },
  });
  expect(runtimeResp.status()).toBe(201);
  const runtime = (await runtimeResp.json()) as { id: string };
  return { id: runtime.id, name: runtimeName };
}

async function createClaudeAssistantFinalRuntime(
  request: APIRequestContext,
  label: string,
): Promise<{ id: string; name: string }> {
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-claude-assistant-")));
  const runtimeScript = path.join(runtimeDir, "claude_assistant_final_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, os, sys, time",
      "prompt_file = os.environ.get('CODETALK_AGENT_PROMPT_FILE')",
      "if prompt_file:",
      "    open(prompt_file, encoding='utf-8').read()",
      "answer = '## 黑盒测试用例\\n' + ''.join([f'{index}. TC-{index:02d} Assistant 登录场景：前置条件 target 已启动，步骤执行 iSCSI Login 场景 {index}，预期结果可观测。\\n' for index in range(1, 9)])",
      "events = [",
      "  {'type':'system','subtype':'init','session_id':'claude-assistant-session-e2e'},",
      "  {'type':'stream_event','event':{'type':'content_block_delta','delta':{'type':'text_delta','text':'## 黑盒测试用例\\n### partial 应被最终 assistant 替换\\n'}}},",
      "  {'type':'assistant','message':{'role':'assistant','content':[{'type':'text','text':answer}]}},",
      "  {'type':'result','status':'success','session_id':'claude-assistant-session-e2e'},",
      "]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "    time.sleep(0.05)",
      "",
    ].join("\n"),
    "utf8",
  );
  const runtimeName = `${label} ${Date.now()}`;
  const runtimeResp = await request.post(`${backendBase}/api/settings/agent-runtimes`, {
    data: {
      name: runtimeName,
      command: "python3",
      args: [runtimeScript],
      prompt_transport: "claude_print_arg",
      output_mode: "stream_json",
      working_dir_mode: "project",
      fixed_working_dir: "",
      env: {},
      health_command: "",
      timeout_seconds: 20,
      enabled: true,
      completion_mode: "process_exit",
      session_persistence: "none",
    },
  });
  expect(runtimeResp.status()).toBe(201);
  const runtime = (await runtimeResp.json()) as { id: string };
  return { id: runtime.id, name: runtimeName };
}

async function createSlowStreamingRuntime(
  request: APIRequestContext,
  label: string,
): Promise<{ id: string; name: string }> {
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-slow-stream-")));
  const runtimeScript = path.join(runtimeDir, "slow_stream_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, os, sys, time",
      "prompt_file = os.environ.get('CODETALK_AGENT_PROMPT_FILE')",
      "if prompt_file:",
      "    open(prompt_file, encoding='utf-8').read()",
      "print(json.dumps({'type':'system','subtype':'init','session_id':'slow-scroll-session'}, ensure_ascii=False), flush=True)",
      "print(json.dumps({'type':'stream_event','event':{'type':'content_block_start','index':0,'content_block':{'type':'text'}}}, ensure_ascii=False), flush=True)",
      "for index in range(1, 56):",
      "    text = f'scroll-line-{index:02d}: 这是一段用于撑开 AI 线程 reader 的真实流式回答内容，覆盖长对话阅读体验。\\n\\n'",
      "    print(json.dumps({'type':'stream_event','event':{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':text}}}, ensure_ascii=False), flush=True)",
      "    time.sleep(0.015)",
      "time.sleep(0.75)",
      "for index in range(1, 9):",
      "    text = f'late-scroll-token-{index}: 用户上滑后仍在后台追加的内容。\\n\\n'",
      "    print(json.dumps({'type':'stream_event','event':{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':text}}}, ensure_ascii=False), flush=True)",
      "    time.sleep(0.12)",
      "print(json.dumps({'type':'stream_event','event':{'type':'content_block_stop','index':0}}, ensure_ascii=False), flush=True)",
      "print(json.dumps({'type':'result','status':'success','session_id':'slow-scroll-session'}, ensure_ascii=False), flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const runtimeName = `${label} ${Date.now()}`;
  const runtimeResp = await request.post(`${backendBase}/api/settings/agent-runtimes`, {
    data: {
      name: runtimeName,
      command: "python3",
      args: [runtimeScript],
      prompt_transport: "stdin",
      output_mode: "stream_json",
      working_dir_mode: "project",
      fixed_working_dir: "",
      env: {},
      health_command: "",
      timeout_seconds: 20,
      enabled: true,
      completion_mode: "process_exit",
      session_persistence: "none",
    },
  });
  expect(runtimeResp.status()).toBe(201);
  const runtime = (await runtimeResp.json()) as { id: string };
  return { id: runtime.id, name: runtimeName };
}

async function createCodexStdinRuntime(
  request: APIRequestContext,
  label: string,
): Promise<{ id: string; name: string; captureFile: string }> {
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-codex-stdin-")));
  const runtimeScript = path.join(runtimeDir, "fake_codex_stdin_agent.py");
  const captureFile = path.join(runtimeDir, "codex_invocations.jsonl");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, pathlib, sys, time",
      `capture = pathlib.Path(${JSON.stringify(captureFile)})`,
      "args = sys.argv[1:]",
      "stdin = sys.stdin.read()",
      "capture.write_text((capture.read_text(encoding='utf-8') if capture.exists() else '') + json.dumps({'argv': args, 'stdin': stdin}, ensure_ascii=False) + '\\n', encoding='utf-8')",
      "resume = args[args.index('resume') + 1] if 'resume' in args else ''",
      "thread_id = 'codex-e2e-second' if resume else 'codex-e2e-first'",
      "answer = ('resumed:' + resume) if resume else 'fresh codex stdin'",
      "print(json.dumps({'type':'thread.started','thread_id':thread_id}, ensure_ascii=False), flush=True)",
      "time.sleep(0.05)",
      "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':answer}}, ensure_ascii=False), flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const runtimeName = `${label} ${Date.now()}`;
  const runtimeResp = await request.post(`${backendBase}/api/settings/agent-runtimes`, {
    data: {
      name: runtimeName,
      command: "python3",
      args: [runtimeScript],
      prompt_transport: "codex_exec_json",
      output_mode: "stream_json",
      working_dir_mode: "project",
      fixed_working_dir: "",
      env: {},
      health_command: "",
      timeout_seconds: 20,
      enabled: true,
      completion_mode: "process_exit",
      session_persistence: "resume_args",
    },
  });
  expect(runtimeResp.status()).toBe(201);
  const runtime = (await runtimeResp.json()) as { id: string };
  return { id: runtime.id, name: runtimeName, captureFile };
}

async function createClaudeResumeRuntime(
  request: APIRequestContext,
  label: string,
): Promise<{ id: string; name: string; captureFile: string }> {
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-claude-resume-")));
  const runtimeScript = path.join(runtimeDir, "fake_claude_resume_agent.py");
  const captureFile = path.join(runtimeDir, "claude_invocations.jsonl");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, os, pathlib, sys, time",
      `capture = pathlib.Path(${JSON.stringify(captureFile)})`,
      "args = sys.argv[1:]",
      "prompt = args[args.index('-p') + 1] if '-p' in args else ''",
      "prompt_file = pathlib.Path(os.environ['CODETALK_AGENT_PROMPT_FILE']).read_text(encoding='utf-8')",
      "capture.write_text((capture.read_text(encoding='utf-8') if capture.exists() else '') + json.dumps({'argv': args, 'prompt': prompt, 'prompt_file': prompt_file}, ensure_ascii=False) + '\\n', encoding='utf-8')",
      "resume = args[args.index('--resume') + 1] if '--resume' in args else ''",
      "session_id = 'claude-e2e-second' if resume else 'claude-e2e-first'",
      "answer = ('resumed claude:' + resume) if resume else 'fresh claude print'",
      "events = [",
      "  {'type':'system','subtype':'init','session_id':session_id},",
      "  {'type':'assistant','message':{'role':'assistant','content':[{'type':'text','text':answer}]}},",
      "  {'type':'result','status':'success','session_id':session_id},",
      "]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "    time.sleep(0.05)",
      "",
    ].join("\n"),
    "utf8",
  );
  const runtimeName = `${label} ${Date.now()}`;
  const runtimeResp = await request.post(`${backendBase}/api/settings/agent-runtimes`, {
    data: {
      name: runtimeName,
      command: "python3",
      args: [runtimeScript],
      prompt_transport: "claude_print_arg",
      output_mode: "stream_json",
      working_dir_mode: "project",
      fixed_working_dir: "",
      env: {},
      health_command: "",
      timeout_seconds: 20,
      enabled: true,
      completion_mode: "process_exit",
      session_persistence: "resume_args",
    },
  });
  expect(runtimeResp.status()).toBe(201);
  const runtime = (await runtimeResp.json()) as { id: string };
  return { id: runtime.id, name: runtimeName, captureFile };
}

async function createOpenCodeResumeRuntime(
  request: APIRequestContext,
  label: string,
): Promise<{ id: string; name: string; captureFile: string }> {
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-opencode-resume-")));
  const runtimeScript = path.join(runtimeDir, "fake_opencode_resume_agent.py");
  const captureFile = path.join(runtimeDir, "opencode_invocations.jsonl");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, pathlib, sys, time",
      `capture = pathlib.Path(${JSON.stringify(captureFile)})`,
      "args = sys.argv[1:]",
      "prompt = args[-1] if args else ''",
      "capture.write_text((capture.read_text(encoding='utf-8') if capture.exists() else '') + json.dumps({'argv': args, 'prompt': prompt}, ensure_ascii=False) + '\\n', encoding='utf-8')",
      "session = args[args.index('--session') + 1] if '--session' in args else ''",
      "thread_id = 'opencode-e2e-second' if session else 'opencode-e2e-first'",
      "answer = ('resumed opencode:' + session) if session else 'fresh opencode run'",
      "events = [",
      "  {'type':'thread.started','thread_id':thread_id},",
      "  {'type':'message','role':'assistant','content':answer},",
      "  {'type':'result','status':'success','thread_id':thread_id},",
      "]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "    time.sleep(0.05)",
      "",
    ].join("\n"),
    "utf8",
  );
  const runtimeName = `${label} ${Date.now()}`;
  const runtimeResp = await request.post(`${backendBase}/api/settings/agent-runtimes`, {
    data: {
      name: runtimeName,
      command: "python3",
      args: [runtimeScript],
      prompt_transport: "opencode_run_arg",
      output_mode: "auto",
      working_dir_mode: "project",
      fixed_working_dir: "",
      env: {},
      health_command: "",
      timeout_seconds: 20,
      enabled: true,
      completion_mode: "process_exit",
      session_persistence: "resume_args",
    },
  });
  expect(runtimeResp.status()).toBe(201);
  const runtime = (await runtimeResp.json()) as { id: string };
  return { id: runtime.id, name: runtimeName, captureFile };
}

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
  const failingRuntime = await createDeterministicFailingRuntime(request, "AI thread failure runtime");

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
  await page.getByLabel("AI 线程执行器").selectOption({ label: failingRuntime.name });
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

  const alert = page.locator('div[role="alert"]').filter({ hasText: "deterministic AI thread failure" });
  await expect(alert).toBeVisible({ timeout: 20_000 });
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
  expect(exported).toContain("deterministic AI thread failure");
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

  await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(failingRuntime.id)}`);
});

test("keeps Claude tool-result stream blocks out of visible answer and artifact", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-claude-block-")));
  fs.mkdirSync(path.join(repo, "lib", "iscsi"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "iscsi", "iscsi.c"),
    "int iscsi_conn_login_pdu_success_complete(void *arg) { return 0; }\n",
    "utf8",
  );
  const workspaceName = `ai-claude-block-e2e-${Date.now()}`;
  const threadTitle = `${workspaceName} stream block cleanup`;

  const runtime = await createClaudeToolResultBlockRuntime(request, "Claude block cleanup runtime");
  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);

  try {
    await page.goto("/ai", { waitUntil: "domcontentloaded" });
    const projectButton = page.locator("button").filter({ hasText: workspaceName }).first();
    await expect(projectButton).toBeVisible({ timeout: 20_000 });
    await projectButton.hover();
    await projectButton.click();

    await page.getByLabel("AI 线程执行器").selectOption({ label: runtime.name });
    await page.getByPlaceholder(/线程名称/).fill(threadTitle);
    await page.getByRole("button", { name: "新建线程" }).hover();
    await page.getByRole("button", { name: "新建线程" }).click();
    await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
    const threadId = page.url().split("/").pop() ?? "";
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });

    const composer = page.getByLabel("AI 线程消息");
    await composer.click();
    await composer.pressSequentially("针对 iSCSI 登录生成黑盒测试用例");
    await page.keyboard.press("Shift+Enter");
    await composer.pressSequentially("不要把源码搜索过程混入最终答案");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.locator(".ct-codex-message").filter({ hasText: "已生成结构化产物" })).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.locator(".ct-codex-message").filter({ hasText: "TC-08 正常登录变体" })).toHaveCount(0);
    await expect(page.locator(".ct-codex-message").filter({ hasText: "iscsi_conn_login_pdu_success_complete" })).toHaveCount(0);
    await expect(page.locator(".ct-codex-message").filter({ hasText: "AuthMethod=CHAP" })).toHaveCount(0);

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect(processDisclosure.getByText(/默认折叠/)).toBeVisible();
    await expect
      .poll(async () => processDisclosure.evaluate((node) => (node as HTMLDetailsElement).open))
      .toBe(false);
    await expect(processDisclosure.getByText("iscsi_conn_login_pdu_success_complete").first()).not.toBeVisible();
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("iscsi_conn_login_pdu_success_complete").first()).toBeVisible();

    const diagnosticsSummary = page.getByText("生成诊断：默认折叠");
    await expect(diagnosticsSummary).toBeVisible({ timeout: 15_000 });
    await diagnosticsSummary.click();
    await expect(page.getByText("iscsi_conn_login_pdu_success_complete").first()).toBeVisible();

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "下载完整产物" }).hover();
    await page.getByRole("link", { name: "下载完整产物" }).click();
    const download = await downloadPromise;
    const artifactPath = testInfo.outputPath("claude-tool-result-clean-artifact.md");
    await download.saveAs(artifactPath);
    const artifact = fs.readFileSync(artifactPath, "utf8");
    expect(artifact).toContain("## 黑盒测试用例");
    expect(artifact).toContain("TC-08 正常登录变体");
    expect(artifact).not.toContain("iscsi_conn_login_pdu_success_complete");
    expect(artifact).not.toContain("AuthMethod=CHAP");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("已生成结构化产物");
    expect(assistant?.content).not.toContain("TC-08 正常登录变体");
    expect(assistant?.content).not.toContain("iscsi_conn_login_pdu_success_complete");
    expect(assistant?.content).not.toContain("AuthMethod=CHAP");

    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    const restoredProcessDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(restoredProcessDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect(restoredProcessDisclosure.getByText(/默认折叠/)).toBeVisible();
    await expect
      .poll(async () => restoredProcessDisclosure.evaluate((node) => (node as HTMLDetailsElement).open))
      .toBe(false);
    await expect(restoredProcessDisclosure.getByText("iscsi_conn_login_pdu_success_complete").first()).not.toBeVisible();
    await restoredProcessDisclosure.getByText("Agent 过程").click();
    await expect(restoredProcessDisclosure.getByText("iscsi_conn_login_pdu_success_complete").first()).toBeVisible();
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("does not pull the reader to the bottom while the user reviews earlier AI output", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-scroll-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI scroll containment workspace\n", "utf8");
  const workspaceName = `ai-scroll-e2e-${Date.now()}`;
  const threadTitle = `${workspaceName} scroll containment`;
  const runtime = await createSlowStreamingRuntime(request, "Slow scroll runtime");

  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);

  try {
    await page.goto("/ai", { waitUntil: "domcontentloaded" });
    const projectButton = page.locator("button").filter({ hasText: workspaceName }).first();
    await expect(projectButton).toBeVisible({ timeout: 20_000 });
    await projectButton.hover();
    await projectButton.click();

    await page.getByLabel("AI 线程执行器").selectOption({ label: runtime.name });
    await page.getByPlaceholder(/线程名称/).fill(threadTitle);
    await page.getByRole("button", { name: "新建线程" }).hover();
    await page.getByRole("button", { name: "新建线程" }).click();
    await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });

    const composer = page.getByLabel("AI 线程消息");
    await composer.click();
    await composer.pressSequentially("生成一段很长的 iSCSI 登录测试设计说明，用于验证滚动行为");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const reader = page.getByLabel("AI 线程对话内容");
    await expect(page.getByText("scroll-line-45")).toBeVisible({ timeout: 30_000 });
    await expect
      .poll(async () =>
        reader.evaluate((node) => {
          const element = node as HTMLElement;
          return element.scrollHeight > element.clientHeight + 240;
        }),
      )
      .toBeTruthy();

    await reader.hover();
    await page.mouse.wheel(0, -900);
    const detachedMetrics = await reader.evaluate((node) => {
      const element = node as HTMLElement;
      return {
        scrollTop: element.scrollTop,
        distanceFromBottom: element.scrollHeight - element.scrollTop - element.clientHeight,
      };
    });
    expect(detachedMetrics.distanceFromBottom).toBeGreaterThan(96);

    await expect(page.getByText("late-scroll-token-8")).toBeAttached({ timeout: 30_000 });
    const afterLateMetrics = await reader.evaluate((node) => {
      const element = node as HTMLElement;
      return {
        scrollTop: element.scrollTop,
        distanceFromBottom: element.scrollHeight - element.scrollTop - element.clientHeight,
      };
    });
    expect(Math.abs(afterLateMetrics.scrollTop - detachedMetrics.scrollTop)).toBeLessThanOrEqual(4);
    expect(afterLateMetrics.distanceFromBottom).toBeGreaterThan(96);

    const jumpButton = page.getByRole("button", { name: "跳到最新回复" });
    await expect(jumpButton).toBeVisible();
    await jumpButton.hover();
    await jumpButton.click();
    await expect
      .poll(async () =>
        reader.evaluate((node) => {
          const element = node as HTMLElement;
          return element.scrollHeight - element.scrollTop - element.clientHeight;
        }),
      )
      .toBeLessThan(24);
    await expect(page.getByText("late-scroll-token-8")).toBeVisible();
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("uses a Claude result event as the final answer after source lookup", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-claude-result-")));
  fs.mkdirSync(path.join(repo, "lib", "iscsi"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "iscsi", "iscsi.c"),
    "int iscsi_conn_login_pdu_success_complete(void *arg) { return 0; }\n",
    "utf8",
  );
  const workspaceName = `ai-claude-result-e2e-${Date.now()}`;
  const threadTitle = `${workspaceName} result final answer`;

  const runtime = await createClaudeResultFinalRuntime(request, "Claude result final runtime");
  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);

  try {
    await page.goto("/ai", { waitUntil: "domcontentloaded" });
    const projectButton = page.locator("button").filter({ hasText: workspaceName }).first();
    await expect(projectButton).toBeVisible({ timeout: 20_000 });
    await projectButton.hover();
    await projectButton.click();

    await page.getByLabel("AI 线程执行器").selectOption({ label: runtime.name });
    await page.getByPlaceholder(/线程名称/).fill(threadTitle);
    await page.getByRole("button", { name: "新建线程" }).hover();
    await page.getByRole("button", { name: "新建线程" }).click();
    await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
    const threadId = page.url().split("/").pop() ?? "";
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });

    const composer = page.getByLabel("AI 线程消息");
    await composer.click();
    await composer.pressSequentially("针对 iSCSI 登录生成黑盒测试用例");
    await page.keyboard.press("Shift+Enter");
    await composer.pressSequentially("先查源码，再把正式答案作为最终结果输出");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.locator(".ct-codex-message").filter({ hasText: "已生成结构化产物" })).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.locator(".ct-codex-message").filter({ hasText: "TC-08 Result 登录场景" })).toHaveCount(0);
    await expect(page.locator(".ct-codex-message").filter({ hasText: "执行器没有返回有效内容" })).toHaveCount(0);
    await expect(page.locator(".ct-codex-message").filter({ hasText: "iscsi_conn_login_pdu_success_complete" })).toHaveCount(0);

    const diagnosticsSummary = page.getByText("生成诊断：默认折叠");
    await expect(diagnosticsSummary).toBeVisible({ timeout: 15_000 });
    await diagnosticsSummary.click();
    await expect(page.getByText("iscsi_conn_login_pdu_success_complete").first()).toBeVisible();

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "下载完整产物" }).hover();
    await page.getByRole("link", { name: "下载完整产物" }).click();
    const download = await downloadPromise;
    const artifactPath = testInfo.outputPath("claude-result-final-artifact.md");
    await download.saveAs(artifactPath);
    const artifact = fs.readFileSync(artifactPath, "utf8");
    expect(artifact).toContain("## 黑盒测试用例");
    expect(artifact).toContain("TC-08 Result 登录场景");
    expect(artifact).not.toContain("iscsi_conn_login_pdu_success_complete");
    expect(artifact).not.toContain("执行器没有返回有效内容");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("已生成结构化产物");
    expect(assistant?.content).not.toContain("TC-08 Result 登录场景");
    expect(assistant?.content).not.toContain("iscsi_conn_login_pdu_success_complete");
    expect(assistant?.content).not.toContain("执行器没有返回有效内容");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("uses a Claude assistant message as the final answer instead of keeping partial text", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-claude-assistant-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI Claude assistant final e2e workspace\n", "utf8");
  const workspaceName = `ai-claude-assistant-e2e-${Date.now()}`;
  const threadTitle = `${workspaceName} assistant final answer`;

  const runtime = await createClaudeAssistantFinalRuntime(request, "Claude assistant final runtime");
  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);

  try {
    await page.goto("/ai", { waitUntil: "domcontentloaded" });
    const projectButton = page.locator("button").filter({ hasText: workspaceName }).first();
    await expect(projectButton).toBeVisible({ timeout: 20_000 });
    await projectButton.hover();
    await projectButton.click();

    await page.getByLabel("AI 线程执行器").selectOption({ label: runtime.name });
    await page.getByPlaceholder(/线程名称/).fill(threadTitle);
    await page.getByRole("button", { name: "新建线程" }).hover();
    await page.getByRole("button", { name: "新建线程" }).click();
    await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
    const threadId = page.url().split("/").pop() ?? "";
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });

    const composer = page.getByLabel("AI 线程消息");
    await composer.click();
    await composer.pressSequentially("针对 iSCSI 登录生成黑盒测试用例");
    await page.keyboard.press("Shift+Enter");
    await composer.pressSequentially("最终答案用 assistant message 输出");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.locator(".ct-codex-message").filter({ hasText: "已生成结构化产物" })).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.locator(".ct-codex-message").filter({ hasText: "TC-08 Assistant 登录场景" })).toHaveCount(0);
    await expect(page.locator(".ct-codex-message").filter({ hasText: "partial 应被最终 assistant 替换" })).toHaveCount(0);
    await expect(
      page.locator(".ct-codex-message:not(.is-user)").getByRole("heading", { name: "黑盒测试用例" }),
    ).toHaveCount(1);

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "下载完整产物" }).hover();
    await page.getByRole("link", { name: "下载完整产物" }).click();
    const download = await downloadPromise;
    const artifactPath = testInfo.outputPath("claude-assistant-final-artifact.md");
    await download.saveAs(artifactPath);
    const artifact = fs.readFileSync(artifactPath, "utf8");
    expect(artifact.match(/## 黑盒测试用例/g)?.length).toBe(1);
    expect(artifact).toContain("TC-08 Assistant 登录场景");
    expect(artifact).not.toContain("partial 应被最终 assistant 替换");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content.match(/## 黑盒测试用例/g)?.length).toBe(1);
    expect(assistant?.content).toContain("已生成结构化产物");
    expect(assistant?.content).not.toContain("TC-08 Assistant 登录场景");
    expect(assistant?.content).not.toContain("partial 应被最终 assistant 替换");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
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

test("deletes an AI thread from the project thread hub", async ({
  page,
  request,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-delete-hub-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI thread deletion hub e2e workspace\n", "utf8");
  const workspaceName = `ai-delete-hub-e2e-${Date.now()}`;
  const threadTitle = `${workspaceName} removable thread`;

  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);
  const workspace = (await workspaceResp.json()) as { id: string };
  const conversationResp = await request.post(`${backendBase}/api/ai/conversations`, {
    data: {
      scope_type: "workspace",
      scope_id: workspace.id,
      workspace_id: workspace.id,
      memory_namespace: `workspace:${workspace.id}`,
      runtime_type: "builtin_llm",
      agent_runtime_id: null,
      title: threadTitle,
      initial_context: {
        workspace_id: workspace.id,
        project_name: workspaceName,
        memory_namespace: `workspace:${workspace.id}`,
      },
    },
  });
  expect(conversationResp.status()).toBe(201);
  const conversation = (await conversationResp.json()) as { id: string };

  await page.goto("/ai", { waitUntil: "domcontentloaded" });
  const projectButton = page.locator("button.ct-thread-project").filter({ hasText: workspaceName }).first();
  await expect(projectButton).toBeVisible({ timeout: 20_000 });
  await projectButton.hover();
  await projectButton.click();

  const threadCard = page.locator(".ct-thread-card").filter({ hasText: threadTitle });
  await expect(threadCard).toBeVisible({ timeout: 15_000 });
  page.once("dialog", async (dialog) => {
    expect(dialog.message()).toContain(threadTitle);
    await dialog.accept();
  });
  await threadCard.hover();
  await page.getByRole("button", { name: `删除线程 ${threadTitle}` }).click();
  await expect(threadCard).toHaveCount(0);

  const listResp = await request.get(`${backendBase}/api/ai/conversations?workspace_id=${workspace.id}&limit=10`);
  expect(listResp.ok()).toBeTruthy();
  const conversations = (await listResp.json()) as { items: Array<{ id: string; title: string }> };
  expect(conversations.items).not.toEqual(
    expect.arrayContaining([expect.objectContaining({ id: conversation.id })]),
  );
});

test("deletes the current AI thread from the detail sidebar and falls back to a sibling thread", async ({
  page,
  request,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-delete-detail-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI thread deletion detail e2e workspace\n", "utf8");
  const workspaceName = `ai-delete-detail-e2e-${Date.now()}`;
  const keepThreadTitle = `${workspaceName} kept sibling`;
  const deleteThreadTitle = `${workspaceName} delete current`;

  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);
  const workspace = (await workspaceResp.json()) as { id: string };

  const keepResp = await request.post(`${backendBase}/api/ai/conversations`, {
    data: {
      scope_type: "workspace",
      scope_id: workspace.id,
      workspace_id: workspace.id,
      memory_namespace: `workspace:${workspace.id}`,
      runtime_type: "builtin_llm",
      agent_runtime_id: null,
      title: keepThreadTitle,
      initial_context: {
        workspace_id: workspace.id,
        project_name: workspaceName,
        memory_namespace: `workspace:${workspace.id}`,
      },
    },
  });
  expect(keepResp.status()).toBe(201);
  const keepThread = (await keepResp.json()) as { id: string };

  const deleteResp = await request.post(`${backendBase}/api/ai/conversations`, {
    data: {
      scope_type: "workspace",
      scope_id: workspace.id,
      workspace_id: workspace.id,
      memory_namespace: `workspace:${workspace.id}`,
      runtime_type: "builtin_llm",
      agent_runtime_id: null,
      title: deleteThreadTitle,
      initial_context: {
        workspace_id: workspace.id,
        project_name: workspaceName,
        memory_namespace: `workspace:${workspace.id}`,
      },
    },
  });
  expect(deleteResp.status()).toBe(201);
  const deletedThread = (await deleteResp.json()) as { id: string };

  await page.goto(`/ai/${deletedThread.id}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: deleteThreadTitle, exact: true })).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByRole("link", { name: new RegExp(keepThreadTitle) })).toBeVisible({
    timeout: 15_000,
  });

  page.once("dialog", async (dialog) => {
    expect(dialog.message()).toContain(deleteThreadTitle);
    await dialog.accept();
  });
  await page.getByRole("button", { name: `删除线程 ${deleteThreadTitle}` }).hover();
  await page.getByRole("button", { name: `删除线程 ${deleteThreadTitle}` }).click();

  await page.waitForURL(new RegExp(`/ai/${keepThread.id}$`), { timeout: 15_000 });
  await expect(page.getByRole("heading", { name: keepThreadTitle, exact: true })).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByRole("link", { name: new RegExp(deleteThreadTitle) })).toHaveCount(0);

  const deletedGet = await request.get(
    `${backendBase}/api/ai/conversations/${encodeURIComponent(deletedThread.id)}`,
  );
  expect(deletedGet.status()).toBe(404);
});

test("contains large real AI project and thread lists inside scroll panes", async ({
  page,
  request,
}) => {
  test.setTimeout(120_000);
  const stamp = Date.now();

  for (let index = 0; index < 12; index += 1) {
    const extraRepo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), `codetalk-ai-list-extra-${index}-`)));
    fs.writeFileSync(path.join(extraRepo, "README.md"), `AI list extra workspace ${index}\n`, "utf8");
    const extraWorkspaceResp = await request.post(`${backendBase}/api/workspaces`, {
      data: { name: `ai-list-extra-${stamp}-${index}`, repo_path: extraRepo },
    });
    expect(extraWorkspaceResp.status()).toBe(201);
  }

  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-list-target-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI list containment target workspace\n", "utf8");
  const workspaceName = `ai-list-target-${stamp}`;
  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);
  const workspace = (await workspaceResp.json()) as { id: string };

  const threadTitles: string[] = [];
  for (let index = 0; index < 34; index += 1) {
    const title = `${workspaceName} thread ${String(index + 1).padStart(2, "0")}`;
    threadTitles.push(title);
    const conversationResp = await request.post(`${backendBase}/api/ai/conversations`, {
      data: {
        scope_type: "workspace",
        scope_id: workspace.id,
        workspace_id: workspace.id,
        memory_namespace: `workspace:${workspace.id}`,
        runtime_type: "builtin_llm",
        agent_runtime_id: null,
        title,
        initial_context: {
          workspace_id: workspace.id,
          project_name: workspaceName,
          memory_namespace: `workspace:${workspace.id}`,
        },
      },
    });
    expect(conversationResp.status()).toBe(201);
  }

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/ai", { waitUntil: "domcontentloaded" });
  const projectButton = page.locator("button.ct-thread-project").filter({ hasText: workspaceName }).first();
  await expect(projectButton).toBeVisible({ timeout: 20_000 });
  await projectButton.hover();
  await projectButton.click();
  await expect(page.getByRole("heading", { name: workspaceName, exact: true })).toBeVisible();
  await expect(page.locator(".ct-thread-card")).toHaveCount(34);

  const homeMetrics = await page.evaluate(() => {
    const projectList = document.querySelector(".ct-ai-home__project-list") as HTMLElement | null;
    const threadTimeline = document.querySelector(".ct-thread-timeline") as HTMLElement | null;
    const home = document.querySelector(".ct-ai-home") as HTMLElement | null;
    return {
      windowScrollY: window.scrollY,
      documentScrollHeight: document.documentElement.scrollHeight,
      viewportHeight: window.innerHeight,
      homeHeight: home?.getBoundingClientRect().height ?? 0,
      projectClientHeight: projectList?.clientHeight ?? 0,
      projectScrollHeight: projectList?.scrollHeight ?? 0,
      projectOverflowY: projectList ? window.getComputedStyle(projectList).overflowY : "",
      threadClientHeight: threadTimeline?.clientHeight ?? 0,
      threadScrollHeight: threadTimeline?.scrollHeight ?? 0,
      threadOverflowY: threadTimeline ? window.getComputedStyle(threadTimeline).overflowY : "",
    };
  });
  expect(homeMetrics.documentScrollHeight).toBeLessThanOrEqual(homeMetrics.viewportHeight + 40);
  expect(homeMetrics.homeHeight).toBeLessThanOrEqual(homeMetrics.viewportHeight);
  expect(homeMetrics.projectScrollHeight).toBeGreaterThan(homeMetrics.projectClientHeight + 120);
  expect(homeMetrics.threadScrollHeight).toBeGreaterThan(homeMetrics.threadClientHeight + 120);
  expect(homeMetrics.projectOverflowY).toBe("auto");
  expect(homeMetrics.threadOverflowY).toBe("auto");
  expect(homeMetrics.windowScrollY).toBe(0);

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

  const newestThreadTitle = threadTitles[threadTitles.length - 1];
  const newestThread = page.getByRole("link", { name: new RegExp(newestThreadTitle) });
  await newestThread.scrollIntoViewIfNeeded();
  await newestThread.hover();
  await newestThread.click();
  await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
  await expect(page.getByRole("heading", { name: newestThreadTitle, exact: true })).toBeVisible({ timeout: 15_000 });

  const threadPageMetrics = await page.evaluate(() => {
    const threadList = document.querySelector(".ct-codex-ai__thread-list") as HTMLElement | null;
    const shell = document.querySelector(".ct-codex-ai") as HTMLElement | null;
    return {
      documentScrollHeight: document.documentElement.scrollHeight,
      viewportHeight: window.innerHeight,
      shellHeight: shell?.getBoundingClientRect().height ?? 0,
      threadClientHeight: threadList?.clientHeight ?? 0,
      threadScrollHeight: threadList?.scrollHeight ?? 0,
      threadOverflowY: threadList ? window.getComputedStyle(threadList).overflowY : "",
    };
  });
  expect(threadPageMetrics.documentScrollHeight).toBeLessThanOrEqual(threadPageMetrics.viewportHeight + 40);
  expect(threadPageMetrics.shellHeight).toBeLessThanOrEqual(threadPageMetrics.viewportHeight);
  expect(threadPageMetrics.threadScrollHeight).toBeGreaterThan(threadPageMetrics.threadClientHeight + 120);
  expect(threadPageMetrics.threadOverflowY).toBe("auto");

  const sidebarThreadList = page.locator(".ct-codex-ai__thread-list");
  await sidebarThreadList.hover();
  await page.mouse.wheel(0, 1200);
  await expect.poll(() => sidebarThreadList.evaluate((element) => element.scrollTop)).toBeGreaterThan(120);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeLessThan(5);
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
  const failingRuntime = await createDeterministicFailingRuntime(request, "AI action failure runtime");

  await page.goto("/ai", { waitUntil: "domcontentloaded" });
  const projectButton = page.locator("button").filter({ hasText: workspaceName }).first();
  await expect(projectButton).toBeVisible({ timeout: 15_000 });
  await projectButton.hover();
  await projectButton.click();
  await page.getByLabel("AI 线程执行器").selectOption({ label: failingRuntime.name });
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
  await expect(page.locator('div[role="alert"]').filter({ hasText: "deterministic AI thread failure" })).toBeVisible({
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

  await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(failingRuntime.id)}`);
});

test("Codex agent runtime reads prompts from stdin and resumes through the real AI thread UI", async ({
  page,
  request,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-codex-stdin-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Codex stdin transport e2e workspace\n", "utf8");
  const workspaceName = `ai-codex-stdin-e2e-${Date.now()}`;
  const threadTitle = `${workspaceName} codex stdin`;
  const runtime = await createCodexStdinRuntime(request, "Codex stdin runtime");

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

    await page.getByLabel("AI 线程执行器").selectOption({ label: runtime.name });
    await page.getByPlaceholder(/线程名称/).fill(threadTitle);
    await page.getByRole("button", { name: "新建线程" }).hover();
    await page.getByRole("button", { name: "新建线程" }).click();

    await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
    const threadId = page.url().split("/").pop() ?? "";
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);

    const firstPrompt = "第一轮：请读取工作区源码并说明 Codex transport stdin";
    const composer = page.getByPlaceholder(/像 Codex 一样继续追问/);
    await composer.fill(firstPrompt);
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "fresh codex stdin" })).toBeVisible({
      timeout: 20_000,
    });

    const secondPrompt = "第二轮：继续沿用上一轮 session，只输出 resume 证据";
    await composer.fill(secondPrompt);
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "resumed:codex-e2e-first" })).toBeVisible({
      timeout: 20_000,
    });

    const captured = fs.readFileSync(runtime.captureFile, "utf8")
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line) as { argv: string[]; stdin: string });
    expect(captured).toHaveLength(2);
    expect(captured[0].argv).toContain("exec");
    expect(captured[0].argv).toContain("--json");
    expect(captured[0].argv).not.toContain(firstPrompt);
    expect(captured[0].stdin).toContain(firstPrompt);
    expect(captured[1].argv).toEqual(expect.arrayContaining(["exec", "resume", "codex-e2e-first", "--json"]));
    expect(captured[1].argv.join(" ")).not.toContain(secondPrompt);
    expect(captured[1].stdin).toContain(secondPrompt);

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    expect(messageBody.items).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ role: "assistant", content: "fresh codex stdin" }),
        expect.objectContaining({ role: "assistant", content: "resumed:codex-e2e-first" }),
      ]),
    );
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
    await request.delete(`${backendBase}/api/workspaces/${encodeURIComponent(workspace.id)}`);
  }
});

test("Claude-style agent runtime resumes the previous CLI session through the real AI thread UI", async ({
  page,
  request,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-claude-resume-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Claude resume transport e2e workspace\n", "utf8");
  const workspaceName = `ai-claude-resume-e2e-${Date.now()}`;
  const threadTitle = `${workspaceName} claude resume`;
  const runtime = await createClaudeResumeRuntime(request, "Claude resume runtime");

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

    await page.getByLabel("AI 线程执行器").selectOption({ label: runtime.name });
    await page.getByPlaceholder(/线程名称/).fill(threadTitle);
    await page.getByRole("button", { name: "新建线程" }).hover();
    await page.getByRole("button", { name: "新建线程" }).click();

    await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
    const threadId = page.url().split("/").pop() ?? "";
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);

    const firstPrompt = "第一轮：请读取工作区源码并建立 Claude session";
    const composer = page.getByPlaceholder(/像 Codex 一样继续追问/);
    await composer.fill(firstPrompt);
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "fresh claude print" })).toBeVisible({
      timeout: 20_000,
    });

    const secondPrompt = "第二轮：沿用 Claude session，只输出 resume 证据";
    await composer.fill(secondPrompt);
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "resumed claude:claude-e2e-first" })).toBeVisible({
      timeout: 20_000,
    });

    const captured = fs.readFileSync(runtime.captureFile, "utf8")
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line) as { argv: string[]; prompt: string; prompt_file: string });
    expect(captured).toHaveLength(2);
    expect(captured[0].argv).toEqual(expect.arrayContaining(["--output-format", "stream-json", "--include-partial-messages", "--verbose"]));
    expect(captured[0].argv).not.toContain("--resume");
    expect(captured[0].prompt).toContain(firstPrompt);
    expect(captured[0].prompt_file).toContain(firstPrompt);
    expect(captured[1].argv).toEqual(expect.arrayContaining(["--resume", "claude-e2e-first", "-p"]));
    expect(captured[1].prompt).toContain(secondPrompt);
    expect(captured[1].prompt_file).toContain(secondPrompt);

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    expect(messageBody.items).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ role: "assistant", content: "fresh claude print" }),
        expect.objectContaining({ role: "assistant", content: "resumed claude:claude-e2e-first" }),
      ]),
    );
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
    await request.delete(`${backendBase}/api/workspaces/${encodeURIComponent(workspace.id)}`);
  }
});

test("OpenCode agent runtime resumes the previous CLI session through the real AI thread UI", async ({
  page,
  request,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-opencode-resume-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "OpenCode resume transport e2e workspace\n", "utf8");
  const workspaceName = `ai-opencode-resume-e2e-${Date.now()}`;
  const threadTitle = `${workspaceName} opencode resume`;
  const runtime = await createOpenCodeResumeRuntime(request, "OpenCode resume runtime");

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

    await page.getByLabel("AI 线程执行器").selectOption({ label: runtime.name });
    await page.getByPlaceholder(/线程名称/).fill(threadTitle);
    await page.getByRole("button", { name: "新建线程" }).hover();
    await page.getByRole("button", { name: "新建线程" }).click();

    await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
    const threadId = page.url().split("/").pop() ?? "";
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);

    const firstPrompt = "第一轮：请读取工作区源码并建立 OpenCode session";
    const composer = page.getByPlaceholder(/像 Codex 一样继续追问/);
    await composer.fill(firstPrompt);
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "fresh opencode run" })).toBeVisible({
      timeout: 20_000,
    });

    const secondPrompt = "第二轮：沿用 OpenCode session，只输出 resume 证据";
    await composer.fill(secondPrompt);
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "resumed opencode:opencode-e2e-first" })).toBeVisible({
      timeout: 20_000,
    });

    const captured = fs.readFileSync(runtime.captureFile, "utf8")
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line) as { argv: string[]; prompt: string });
    expect(captured).toHaveLength(2);
    expect(captured[0].argv.slice(0, 3)).toEqual(["run", "--format", "json"]);
    expect(captured[0].argv).not.toContain("--session");
    expect(captured[0].prompt).toContain(firstPrompt);
    expect(captured[1].argv.slice(0, 5)).toEqual(["run", "--session", "opencode-e2e-first", "--format", "json"]);
    expect(captured[1].prompt).toContain(secondPrompt);

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    expect(messageBody.items).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ role: "assistant", content: "fresh opencode run" }),
        expect.objectContaining({ role: "assistant", content: "resumed opencode:opencode-e2e-first" }),
      ]),
    );
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
    await request.delete(`${backendBase}/api/workspaces/${encodeURIComponent(workspace.id)}`);
  }
});

test("renders native OpenCode tool and error events as Agent process diagnostics", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-opencode-native-")));
  fs.writeFileSync(path.join(repo, "README.md"), "OpenCode native event e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-opencode-native-")));
  const runtimeScript = path.join(runtimeDir, "opencode_native_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, sys, time",
      "prompt = sys.argv[-1] if sys.argv else ''",
      "events = [",
      "  {'type':'step_start','timestamp':1,'sessionID':'opencode-native-e2e'},",
      "  {'type':'tool_use','timestamp':2,'sessionID':'opencode-native-e2e','part':{'type':'tool_use','tool':'grep','state':{'input':{'pattern':'spdk_nvmf','path':'lib/nvmf'}}}},",
      "  {'type':'error','timestamp':3,'sessionID':'opencode-native-e2e','error':{'name':'OpenCodeToolWarning','data':{'message':'opencode grep warning while reading lib/nvmf'}}},",
      "  {'type':'text','timestamp':4,'sessionID':'opencode-native-e2e','part':{'type':'text','text':'OPENCODE_NATIVE_FINAL: 已基于源码线索完成分析。'}},",
      "  {'type':'step_finish','timestamp':5,'sessionID':'opencode-native-e2e'},",
      "]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "    time.sleep(0.05)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-opencode-native-e2e-${Date.now()}`;
  const runtimeName = `OpenCode native runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} native events`;

  const runtimeResp = await request.post(`${backendBase}/api/settings/agent-runtimes`, {
    data: {
      name: runtimeName,
      command: "python3",
      args: [runtimeScript],
      prompt_transport: "opencode_run_arg",
      output_mode: "auto",
      working_dir_mode: "project",
      fixed_working_dir: "",
      env: {},
      health_command: "",
      timeout_seconds: 30,
      enabled: true,
      completion_mode: "process_exit",
      session_persistence: "resume_args",
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
    await expect(projectButton).toBeVisible({ timeout: 20_000 });
    await projectButton.hover();
    await projectButton.click();

    await page.getByLabel("AI 线程执行器").selectOption({ label: runtimeName });
    await page.getByPlaceholder(/线程名称/).fill(threadTitle);
    await page.getByRole("button", { name: "新建线程" }).hover();
    await page.getByRole("button", { name: "新建线程" }).click();
    await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
    const threadId = page.url().split("/").pop() ?? "";
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });

    await page.getByLabel("AI 线程消息").fill("请用 OpenCode 原生事件读取源码并只展示最终答案");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "OPENCODE_NATIVE_FINAL" })).toBeVisible({
      timeout: 20_000,
    });
    const answer = page.locator(".ct-codex-message:not(.is-user)");
    await expect(answer.filter({ hasText: "TOOL:" })).toHaveCount(0);
    await expect(answer.filter({ hasText: "opencode grep warning" })).toHaveCount(0);

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect(processDisclosure.getByText("grep")).toBeHidden();
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText(/grep .*spdk_nvmf/)).toBeVisible({ timeout: 15_000 });
    await expect(processDisclosure.getByText("opencode grep warning while reading lib/nvmf")).toBeVisible();

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("OPENCODE_NATIVE_FINAL");
    expect(assistant?.content).not.toContain("opencode grep warning");
    expect(assistant?.content).not.toContain("TOOL:");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
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
}, testInfo) => {
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
      "print('  internal multiline note: select evidence cards before answering', flush=True)",
      "print('  internal multiline note: avoid exposing chain-of-thought', flush=True)",
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
    await expect(reader).not.toContainText("internal multiline note");
    await expect(reader).not.toContainText("chain-of-thought-like internal note");
    await expect(page.getByText("生成诊断：默认折叠")).toBeVisible();
    await expect(page.getByText("reading workspace source evidence")).toBeHidden();
    await expect(page.getByText("internal multiline note: select evidence cards")).toBeHidden();
    await expect(page.getByText("internal multiline note: avoid exposing")).toBeHidden();
    await expect(page.getByText("chain-of-thought-like internal note")).toBeHidden();

    await page.getByText("生成诊断：默认折叠").click();
    await expect(page.getByText("reading workspace source evidence")).toBeVisible();
    await expect(page.getByText("internal multiline note: select evidence cards")).toBeVisible();
    await expect(page.getByText("internal multiline note: avoid exposing")).toBeVisible();
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
    expect(assistantMessages[0].content).not.toContain("internal multiline note");
    expect(assistantMessages[0].content).not.toContain("chain-of-thought-like internal note");

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出" }).click();
    const download = await downloadPromise;
    const exportPath = testInfo.outputPath("real-ai-thread-diagnostic-export.md");
    await download.saveAs(exportPath);
    const exported = fs.readFileSync(exportPath, "utf8");
    expect(exported).toContain("FINAL_DIAGNOSTIC_ANSWER");
    expect(exported).not.toContain("thinking:");
    expect(exported).not.toContain("diagnostic:");
    expect(exported).not.toContain("internal multiline note");
    expect(exported).not.toContain("chain-of-thought-like internal note");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("keeps an expanded Agent process disclosure open while diagnostics continue streaming", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-process-open-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI process disclosure streaming e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-process-open-agent-")));
  const runtimeScript = path.join(runtimeDir, "process_open_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys, time",
      "sys.stdin.read()",
      "for index in range(1, 5):",
      "    print(f'thinking: PROCESS_OPEN_DIAG_{index:02d} reading workspace evidence', flush=True)",
      "    time.sleep(0.18)",
      "print('## 结论', flush=True)",
      "print('PROCESS_OPEN_FINAL_ANSWER 已完成源码分析。', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-process-open-e2e-${Date.now()}`;
  const runtimeName = `Process disclosure runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} process open`;

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

    await page.getByLabel("AI 线程消息").fill("PROCESS_OPEN_RUN 请分析源码并持续展示 agent 过程");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect
      .poll(async () => processDisclosure.evaluate((node) => (node as HTMLDetailsElement).open))
      .toBe(false);
    await processDisclosure.getByText("Agent 过程").click();
    await expect
      .poll(async () => processDisclosure.evaluate((node) => (node as HTMLDetailsElement).open))
      .toBe(true);

    await expect(processDisclosure.getByText("PROCESS_OPEN_DIAG_04")).toBeVisible({ timeout: 20_000 });
    await expect
      .poll(async () => processDisclosure.evaluate((node) => (node as HTMLDetailsElement).open))
      .toBe(true);

    await expect(page.getByText("PROCESS_OPEN_FINAL_ANSWER")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0, { timeout: 15_000 });
    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "PROCESS_OPEN_DIAG_04" })).toHaveCount(0);
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

test("folds mixed JSON agent tool and thinking parts while showing only the answer", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-json-parts-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI JSON part folding e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-json-parts-")));
  const runtimeScript = path.join(runtimeDir, "json_parts_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json",
      "import sys",
      "sys.stdin.read()",
      "event = {",
      "  'type': 'message',",
      "  'role': 'assistant',",
      "  'content': [",
      "    {'type': 'thinking', 'text': '内部推理：先列出工具计划'},",
      "    {'type': 'tool_result', 'content': 'cat /secret/path returned internal-only trace'},",
      "    {'type': 'text', 'text': 'FINAL_JSON_PARTS_ANSWER: 只展示源码分析结论。'},",
      "  ],",
      "}",
      "print(json.dumps(event, ensure_ascii=False), flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-json-parts-e2e-${Date.now()}`;
  const runtimeName = `JSON parts runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} folded json parts`;
  const prompt = "JSON_PARTS_RUN 请运行 agent，但不要把工具过程混进最终回答";

  const runtimeResp = await request.post(`${backendBase}/api/settings/agent-runtimes`, {
    data: {
      name: runtimeName,
      command: "python3",
      args: [runtimeScript],
      prompt_transport: "stdin",
      output_mode: "auto",
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
    await expect(page.getByText("FINAL_JSON_PARTS_ANSWER")).toBeVisible({ timeout: 30_000 });
    const reader = page.getByLabel("AI 线程对话内容");
    await expect(reader).not.toContainText("内部推理：先列出工具计划");
    await expect(reader).not.toContainText("secret/path");
    await expect(page.getByText("生成诊断：默认折叠")).toBeVisible();
    await expect(page.getByText("内部推理：先列出工具计划")).toBeHidden();
    await expect(page.getByText("cat /secret/path returned internal-only trace")).toBeHidden();

    await page.getByText("生成诊断：默认折叠").click();
    await expect(page.getByText("内部推理：先列出工具计划")).toBeVisible();
    await expect(page.getByText("cat /secret/path returned internal-only trace")).toBeVisible();

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as {
      items: Array<{ role: string; content: string }>;
    };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("FINAL_JSON_PARTS_ANSWER");
    expect(assistant?.content).not.toContain("内部推理");
    expect(assistant?.content).not.toContain("secret/path");

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出" }).hover();
    await page.getByRole("button", { name: "导出" }).click();
    const download = await downloadPromise;
    const exportPath = testInfo.outputPath("real-ai-thread-json-parts-export.md");
    await download.saveAs(exportPath);
    const exported = fs.readFileSync(exportPath, "utf8");
    expect(exported).toContain("FINAL_JSON_PARTS_ANSWER");
    expect(exported).not.toContain("内部推理");
    expect(exported).not.toContain("secret/path");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("contains long unbroken AI thread text without right-edge clipping", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-long-token-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI long token layout e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-long-token-")));
  const runtimeScript = path.join(runtimeDir, "long_token_agent.py");
  const longAnswerToken =
    "lib/nvmf/" +
    "connect_timeout_reconnect_controller_reset_evidence_path_segment_".repeat(8) +
    "ctrlr.c";
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "sys.stdin.read()",
      `print('FINAL_LONG_TOKEN_LAYOUT_ANSWER: ${longAnswerToken}', flush=True)`,
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-long-token-e2e-${Date.now()}`;
  const runtimeName = `Long token layout runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} no right clipping`;
  const longPromptToken = "USER_LONG_TOKEN_" + "spdk_nvmf_connect_io_timeout_reconnect_".repeat(9);
  const prompt = `LONG_TOKEN_LAYOUT_RUN ${longPromptToken}`;

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
    await page.setViewportSize({ width: 1180, height: 820 });
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

    await page.getByLabel("AI 线程消息").fill(prompt);
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("FINAL_LONG_TOKEN_LAYOUT_ANSWER")).toBeVisible({ timeout: 30_000 });

    const layout = await page.locator(".ct-codex-ai__reader").evaluate((reader) => {
      const readerRect = reader.getBoundingClientRect();
      const nodes = Array.from(reader.querySelectorAll(".ct-codex-message__content, .ct-codex-message__content > div, .ct-codex-message__content p, .ct-codex-message__content code"));
      return nodes.map((node) => {
        const rect = node.getBoundingClientRect();
        return {
          text: (node.textContent ?? "").slice(0, 120),
          left: rect.left,
          right: rect.right,
          width: rect.width,
          scrollWidth: (node as HTMLElement).scrollWidth,
          clientWidth: (node as HTMLElement).clientWidth,
          readerLeft: readerRect.left,
          readerRight: readerRect.right,
        };
      });
    });
    const overflowing = layout.filter(
      (box) =>
        box.width > 1 &&
        (box.left < box.readerLeft - 1 ||
          box.right > box.readerRight + 1 ||
          box.scrollWidth > box.clientWidth + 1),
    );
    expect(overflowing).toEqual([]);
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

test("keeps an incomplete structured agent answer visible with folded quality diagnostics", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-quality-warning-repo-")));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "nvmf", "ctrlr.c"),
    "int nvmf_ctrlr_connect_quality_warning(void) { return 0; }\n",
    "utf8",
  );
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-quality-warning-")));
  const runtimeScript = path.join(runtimeDir, "quality_warning_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "sys.stdin.read()",
      "print('QUALITY_WARNING_VISIBLE_ANSWER', flush=True)",
      "print('Evidence: lib/nvmf/ctrlr.c nvmf_ctrlr_connect_quality_warning', flush=True)",
      "print('Flow: connect request -> controller setup -> IO queue ready', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-quality-warning-e2e-${Date.now()}`;
  const runtimeName = `Quality warning runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} structured soft warning`;
  const prompt = "分析 SPDK NVMe-oF target connect，并输出代码证据、流程梳理、SFMEA 和黑盒测试用例";

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
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);

    await page.getByLabel("AI 线程消息").fill(prompt);
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("QUALITY_WARNING_VISIBLE_ANSWER")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Evidence: lib/nvmf/ctrlr.c nvmf_ctrlr_connect_quality_warning")).toBeVisible();
    await expect(page.locator("div[role='alert']").filter({ hasText: "Agent 返回内容不足" })).toHaveCount(0);
    await expect(page.getByText("仍未完全满足本轮源码分析验收项")).toBeHidden();

    await page.getByText("生成诊断：默认折叠").click();
    await expect(page.getByText("仍未完全满足本轮源码分析验收项")).toBeVisible();
    await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0, { timeout: 15_000 });

    const conversationResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}`,
    );
    expect(conversationResp.ok()).toBeTruthy();
    const conversation = (await conversationResp.json()) as {
      status: string;
      latest_run: { status: string } | null;
    };
    expect(conversation.status).toBe("idle");
    expect(conversation.latest_run?.status).toBe("completed");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as {
      items: Array<{ role: string; content: string }>;
    };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("QUALITY_WARNING_VISIBLE_ANSWER");
    expect(assistant?.content).toContain("lib/nvmf/ctrlr.c");
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

test("injects UI-added workspace materials and source into an agent-runtime AI thread", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-material-source-")));
  const sourcePath = path.join(repo, "lib", "nvmf", "material_probe.c");
  const materialPath = path.join(repo, "requirements.md");
  fs.mkdirSync(path.dirname(sourcePath), { recursive: true });
  fs.writeFileSync(
    sourcePath,
    [
      "int codetalk_workspace_source_material_probe(void) {",
      "    return 271828;",
      "}",
      "",
    ].join("\n"),
    "utf8",
  );
  fs.writeFileSync(
    materialPath,
    [
      "# Requirements",
      "",
      "REQUIREMENT_SENTINEL_RECONNECT_TIMEOUT must be covered before black-box cases.",
      "",
    ].join("\n"),
    "utf8",
  );
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-material-source-")));
  const runtimeScript = path.join(runtimeDir, "material_source_asserting_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "prompt = sys.stdin.read()",
      "required = [",
      "    'SOURCE_FIRST_CONTRACT',",
      "    'workspace_sources',",
      "    'workspace_materials',",
      "    'lib/nvmf/material_probe.c',",
      "    'codetalk_workspace_source_material_probe',",
      "    'return 271828;',",
      "    'requirements.md',",
      "    'REQUIREMENT_SENTINEL_RECONNECT_TIMEOUT',",
      "]",
      "missing = [item for item in required if item not in prompt]",
      "if missing:",
      "    print('MATERIAL_SOURCE_CONTEXT_MISSING ' + ','.join(missing), flush=True)",
      "else:",
      "    print('MATERIAL_SOURCE_CONTEXT_OK requirements.md lib/nvmf/material_probe.c', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-material-source-${Date.now()}`;
  const runtimeName = `Material source runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} material source`;
  const prompt = "请分析 lib/nvmf/material_probe.c，并结合 requirements.md 生成黑盒测试重点";

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
    await page.goto(`/workspaces/${workspace.id}`, { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: workspaceName })).toBeVisible({ timeout: 15_000 });
    await page.getByRole("button", { name: /材料 \(0\)/ }).hover();
    await page.getByRole("button", { name: /材料 \(0\)/ }).click();
    await page.getByPlaceholder(/输入文件绝对路径/).fill(materialPath);
    await page.getByRole("button", { name: "添加" }).hover();
    await page.getByRole("button", { name: "添加" }).click();
    await expect(page.getByRole("button", { name: /材料 \(1\)/ })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("requirements.md")).toBeVisible();
    await expect(page.getByText("1 个活跃材料将参与分析")).toBeVisible();

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
    await expect(page.getByText("MATERIAL_SOURCE_CONTEXT_OK requirements.md lib/nvmf/material_probe.c")).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText("MATERIAL_SOURCE_CONTEXT_MISSING")).toHaveCount(0);

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const body = (await messagesResp.json()) as {
      items: Array<{
        role: string;
        content: string;
        references?: Array<{ source_type: string; title?: string; metadata?: Record<string, unknown> }>;
      }>;
    };
    const userMessage = body.items.find((item) => item.role === "user" && item.content === prompt);
    expect(userMessage?.references).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          source_type: "workspace_source",
          metadata: expect.objectContaining({ path: "lib/nvmf/material_probe.c" }),
        }),
        expect.objectContaining({
          source_type: "workspace_material",
          title: "requirements.md",
          metadata: expect.objectContaining({ filename: "requirements.md" }),
        }),
      ]),
    );
    expect(JSON.stringify(userMessage?.references ?? [])).not.toContain(repo);
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

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出" }).hover();
    await page.getByRole("button", { name: "导出" }).click();
    const download = await downloadPromise;
    const exportPath = test.info().outputPath("real-ai-thread-default-source-public-path-export.md");
    await download.saveAs(exportPath);
    const exported = fs.readFileSync(exportPath, "utf8");
    expect(exported).toContain("DEFAULT_SOURCE_CONTEXT_OK src/entry.c");
    expect(exported).toContain("源码位置: src/entry.c:L1");
    expect(exported).not.toContain(repo);
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

test("passes a full multiline prompt to a managed Claude-style agent runtime", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-managed-multiline-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI managed multiline e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-managed-multiline-")));
  const runtimeScript = path.join(runtimeDir, "managed_multiline_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import os, pathlib, sys",
      "argv = sys.argv[1:]",
      "prompt_file = pathlib.Path(os.environ['CODETALK_AGENT_PROMPT_FILE']).read_text(encoding='utf-8')",
      "prompt_arg = argv[argv.index('-p') + 1] if '-p' in argv else ''",
      "expected = '第一行：分析 SPDK iSCSI login\\n第二行：输出流程梳理\\n第三行：生成 SFMEA 和黑盒测试用例'",
      "print('MANAGED_MULTILINE_AGENT_REPLY')",
      "print('argv_has_full_multiline=' + str(expected in prompt_arg).lower())",
      "print('prompt_file_has_full_multiline=' + str(expected in prompt_file).lower())",
      "print('argv_line_occurrences=' + str(prompt_arg.count('第一行：分析 SPDK iSCSI login')) + '/' + str(prompt_arg.count('第二行：输出流程梳理')) + '/' + str(prompt_arg.count('第三行：生成 SFMEA 和黑盒测试用例')))",
      "print('prompt_file_line_occurrences=' + str(prompt_file.count('第一行：分析 SPDK iSCSI login')) + '/' + str(prompt_file.count('第二行：输出流程梳理')) + '/' + str(prompt_file.count('第三行：生成 SFMEA 和黑盒测试用例')))",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-managed-multiline-e2e-${Date.now()}`;
  const runtimeName = `Managed multiline Claude runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} managed multiline prompt`;
  const lines = [
    "第一行：分析 SPDK iSCSI login",
    "第二行：输出流程梳理",
    "第三行：生成 SFMEA 和黑盒测试用例",
  ];
  const prompt = lines.join("\n");

  const runtimeResp = await request.post(`${backendBase}/api/settings/agent-runtimes`, {
    data: {
      name: runtimeName,
      command: "python3",
      args: [runtimeScript],
      prompt_transport: "claude_print_arg",
      output_mode: "plain",
      working_dir_mode: "project",
      fixed_working_dir: "",
      env: {},
      health_command: "",
      timeout_seconds: 30,
      enabled: true,
      completion_mode: "process_exit",
      session_persistence: "none",
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
    await expect(projectButton).toBeVisible({ timeout: 20_000 });
    await projectButton.hover();
    await projectButton.click();

    await page.getByLabel("AI 线程执行器").selectOption({ label: runtimeName });
    await page.getByPlaceholder(/线程名称/).fill(threadTitle);
    await page.getByRole("button", { name: "新建线程" }).hover();
    await page.getByRole("button", { name: "新建线程" }).click();
    await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
    const threadId = page.url().split("/").pop() ?? "";
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);

    const composer = page.getByLabel("AI 线程消息");
    await composer.click();
    await composer.pressSequentially(lines[0]);
    await page.keyboard.press("Shift+Enter");
    await composer.pressSequentially(lines[1]);
    await page.keyboard.press("Shift+Enter");
    await composer.pressSequentially(lines[2]);
    await expect(composer).toHaveValue(prompt);

    await page.keyboard.press("Enter");
    for (const line of lines) {
      await expect(page.locator(".ct-codex-message.is-user").filter({ hasText: line })).toHaveCount(1);
    }
    await expect(page.getByText("MANAGED_MULTILINE_AGENT_REPLY")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("argv_has_full_multiline=true")).toBeVisible();
    await expect(page.getByText("prompt_file_has_full_multiline=true")).toBeVisible();
    await expect(page.getByText(/argv_line_occurrences=[1-9]\d*\/[1-9]\d*\/[1-9]\d*/)).toBeVisible();
    await expect(page.getByText(/prompt_file_line_occurrences=[1-9]\d*\/[1-9]\d*\/[1-9]\d*/)).toBeVisible();

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    expect(messageBody.items.filter((item) => item.role === "user" && item.content === prompt)).toHaveLength(1);
    expect(
      messageBody.items.some((item) => item.role === "assistant" && item.content.includes("argv_has_full_multiline=true")),
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

test("shows the thread-bound executor even after that runtime is disabled", async ({
  page,
  request,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-disabled-runtime-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI disabled runtime visibility e2e workspace\n", "utf8");
  const workspaceName = `ai-disabled-runtime-${Date.now()}`;
  const runtimeName = `Disabled runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} disabled runtime`;

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
    const created = await request.post(`${backendBase}/api/ai/conversations`, {
      data: {
        scope_type: "workspace",
        scope_id: workspace.id,
        workspace_id: workspace.id,
        memory_namespace: `workspace:${workspace.id}`,
        runtime_type: "agent_runtime",
        agent_runtime_id: runtime.id,
        title: threadTitle,
      },
    });
    expect(created.status()).toBe(201);
    const conversation = (await created.json()) as { id: string };

    const disabled = await request.put(
      `${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`,
      {
        data: { enabled: false },
      },
    );
    expect(disabled.ok()).toBeTruthy();

    await page.goto(`/ai/${conversation.id}`, { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({
      timeout: 15_000,
    });
    const threadRuntimeSelect = page.getByLabel("当前 AI 执行器");
    await expect(threadRuntimeSelect).toHaveValue(runtime.id);
    await expect(threadRuntimeSelect.locator(`option[value="${runtime.id}"]`)).toContainText(
      `${runtimeName}（已停用）`,
    );
    await expect(threadRuntimeSelect.locator(`option[value="${runtime.id}"]`)).toBeDisabled();
    await expect(page.locator(".ct-ai-env-card").filter({ hasText: "执行器" })).toContainText(runtimeName);
    await expect(page.locator(".ct-ai-env-card").filter({ hasText: "执行器" })).toContainText("已停用");
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
  await expect(page.locator(".ct-codex-ai__context code").filter({ hasText: `workspace:${workspace.id}` })).toBeVisible();
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
  await expect(page.locator(".ct-codex-ai__context code").filter({ hasText: `workspace:${workspace.id}` })).toBeVisible();

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
  await expect(page.locator(".ct-codex-ai__context code").filter({ hasText: `workspace:${workspace.id}` })).toBeVisible();
});
