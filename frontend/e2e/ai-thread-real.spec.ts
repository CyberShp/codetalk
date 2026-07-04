import { expect, test, type APIRequestContext } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;
const frontendPort = process.env.CODETALK_FRONTEND_PORT ?? "3003";
const backendPort = process.env.CODETALK_BACKEND_PORT ?? "3004";

assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "AI thread real E2E",
  frontendPort,
  backendPort,
});

function textIncludesAny(value: unknown, needles: string[]): boolean {
  const text = String(value ?? "").toLowerCase();
  return needles.some((needle) => text.includes(needle.toLowerCase()));
}

function isAiThreadE2ERuntime(runtime: Record<string, unknown>): boolean {
  const runtimePathText = JSON.stringify([
    runtime.command ?? "",
    runtime.args ?? [],
    runtime.health_command ?? "",
    runtime.fixed_working_dir ?? "",
  ]);
  return (
    textIncludesAny(runtime.name, ["e2e", "runtime cancel", "terminal cleanup", "Relevant Evidence"]) ||
    textIncludesAny(runtimePathText, [
      "/tmp/codetalk",
      "codetalk-agent-",
      "codetalk-claude-",
      "codetalk-ai-",
    ])
  );
}

function isAiThreadE2EConversation(conversation: Record<string, unknown>, runtimeIds: Set<string>): boolean {
  const title = String(conversation.title ?? "");
  const runtimeId = String(conversation.agent_runtime_id ?? "");
  return (
    runtimeIds.has(runtimeId) ||
    textIncludesAny(title, [
      "-e2e-",
      "E2E ",
      " E2E",
      "E2E 源码全文折叠验证",
      "Relevant evidence line 验证",
      "SPDK real nvmf clowder 验证",
    ])
  );
}

function isAiThreadE2EWorkspace(workspace: Record<string, unknown>): boolean {
  return (
    textIncludesAny(workspace.name, ["-e2e-", "ai_context_panel_", "entry-discovery-ws-", "release-click-"]) ||
    textIncludesAny(workspace.repo_path, [
      "/codetalk-ai-",
      "/codetalk_ai_context_panel_",
      "/codetalk-entry-ui-",
      "/codetalk-agent-",
    ])
  );
}

async function deleteIfPresent(request: APIRequestContext, url: string): Promise<void> {
  const response = await request.delete(url);
  expect([204, 404, 409]).toContain(response.status());
}

async function cleanupAiThreadE2EData(request: APIRequestContext): Promise<void> {
  const runtimesResp = await request.get(`${backendBase}/api/settings/agent-runtimes`);
  const runtimesBody = runtimesResp.ok()
    ? ((await runtimesResp.json()) as { items?: Array<Record<string, unknown>> })
    : { items: [] };
  const runtimeIds = new Set(
    (runtimesBody.items ?? [])
      .filter(isAiThreadE2ERuntime)
      .map((runtime) => String(runtime.id ?? ""))
      .filter(Boolean),
  );

  const conversationsResp = await request.get(
    `${backendBase}/api/ai/conversations?include_internal=true&limit=100`,
  );
  if (conversationsResp.ok()) {
    const conversationsBody = (await conversationsResp.json()) as { items?: Array<Record<string, unknown>> };
    for (const conversation of conversationsBody.items ?? []) {
      if (!isAiThreadE2EConversation(conversation, runtimeIds)) continue;
      await deleteIfPresent(
        request,
        `${backendBase}/api/ai/conversations/${encodeURIComponent(String(conversation.id ?? ""))}`,
      );
    }
  }

  const workspacesResp = await request.get(`${backendBase}/api/workspaces?include_internal=true`);
  if (workspacesResp.ok()) {
    const workspaces = (await workspacesResp.json()) as Array<Record<string, unknown>>;
    for (const workspace of workspaces) {
      if (!isAiThreadE2EWorkspace(workspace)) continue;
      await deleteIfPresent(
        request,
        `${backendBase}/api/workspaces/${encodeURIComponent(String(workspace.id ?? ""))}`,
      );
    }
  }

  for (const runtimeId of runtimeIds) {
    await deleteIfPresent(
      request,
      `${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtimeId)}`,
    );
  }
}

test.afterEach(async ({ request }) => {
  await cleanupAiThreadE2EData(request);
});

test("cleanup catches temp-path AI thread agent runtimes that do not mention e2e in the name", async ({
  request,
}) => {
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-cleanup-")));
  const runtimeScript = path.join(runtimeDir, "cleanup_agent.py");
  fs.writeFileSync(
    runtimeScript,
    ["import sys", "sys.stdin.read()", "print('cleanup guard')", ""].join("\n"),
    "utf8",
  );
  const runtimeName = `Cleanup escaped runtime ${Date.now()}`;
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

  await cleanupAiThreadE2EData(request);

  const listResp = await request.get(`${backendBase}/api/settings/agent-runtimes`);
  expect(listResp.ok()).toBeTruthy();
  const body = (await listResp.json()) as { items?: Array<{ name?: string }> };
  expect((body.items ?? []).some((runtime) => runtime.name === runtimeName)).toBe(false);
});

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
      "answer = '## 结论\\n已生成结构化产物，正文只保留可交付测试设计。\\n\\n## 代码证据\\n- `lib/iscsi/iscsi.c`: 登录状态机源码文件用于约束测试范围。\\n- `test/iscsi_tgt`: 可承载登录黑盒场景回归。\\n\\n## 流程梳理\\n1. 发起 Login Request。\\n2. 校验认证与参数。\\n3. 返回 Login Response 并进入目标阶段。\\n\\n## 黑盒测试用例\\n' + ''.join([f'{index}. TC-{index:02d} 正常登录变体：前置条件 target 已启动，步骤执行 iSCSI Login 场景 {index}，预期结果进入 Full Feature Phase 或返回明确 Login Response，观测点为响应码、session 状态和日志。\\n' for index in range(1, 9)])",
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

async function createClaudeDeltaWithoutBlockStartRuntime(
  request: APIRequestContext,
  label: string,
): Promise<{ id: string; name: string }> {
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-claude-delta-no-start-")));
  const runtimeScript = path.join(runtimeDir, "claude_delta_no_start_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, os, sys, time",
      "prompt_file = os.environ.get('CODETALK_AGENT_PROMPT_FILE')",
      "if prompt_file:",
      "    open(prompt_file, encoding='utf-8').read()",
      "answer = '## 结论\\n已基于源码证据整理 iSCSI 登录黑盒测试。\\n\\n## 代码证据\\n- `lib/iscsi/iscsi.c`: 登录状态机与 CHAP 参数协商。\\n- `test/iscsi_tgt`: 登录场景可映射到现有测试目录。\\n\\n## 黑盒测试用例\\n1. TC-01 正常登录：前置 target 已启动；步骤发起 Login；预期进入 Full Feature Phase。\\n2. TC-02 CHAP 失败：前置开启 CHAP；步骤使用错误 secret；预期 Login Response 指示认证失败并记录日志。\\n'",
      "events = [",
      "  {'type':'system','subtype':'init','session_id':'claude-delta-no-start-e2e'},",
      "  {'type':'stream_event','event':{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'THINKING: '}}},",
      "  {'type':'stream_event','event':{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'我先核对工作区 iSCSI 登录相关源码，再据此设计黑盒用例。'}}},",
      "  {'type':'stream_event','event':{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'Bash {\"command\": \"grep -n login lib/iscsi/iscsi.c | head -60\"}'}}},",
      "  {'type':'stream_event','event':{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'1125:iscsi_conn_login_pdu_success_complete(void *arg)\\n'}}},",
      "  {'type':'stream_event','event':{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'lib/iscsi/iscsi.c:1539:\\tAuthMethod=CHAP\\n'}}},",
      "  {'type':'stream_event','event':{'type':'content_block_delta','index':1,'delta':{'type':'text_delta','text':answer}}},",
      "  {'type':'result','status':'success','session_id':'claude-delta-no-start-e2e'},",
      "]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "    time.sleep(0.04)",
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

async function createChoiceDeltaProcessRuntime(
  request: APIRequestContext,
  label: string,
): Promise<{ id: string; name: string }> {
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-choice-delta-process-")));
  const runtimeScript = path.join(runtimeDir, "choice_delta_process_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, os, sys, time",
      "sys.stdin.read()",
      "events = [",
      "  {'type':'system','subtype':'init','session_id':'choice-delta-process-e2e'},",
      "  {'choices':[{'delta':{'content':'THINKING: '}}]},",
      "  {'choices':[{'delta':{'content':'我先核对工作区 iSCSI 登录相关源码，再'}}]},",
      "  {'choices':[{'delta':{'content':'据此设计黑盒用例。'}}]},",
      "  {'choices':[{'delta':{'content':'Bash {\"command\": \"grep -n login lib/iscsi/iscsi.c | head -60\"}'}}]},",
      "  {'choices':[{'delta':{'content':'1125:iscsi_conn_login_pdu_success_complete(void *arg)\\n'}}]},",
      "  {'choices':[{'delta':{'content':'lib/iscsi/iscsi.c:1539:\\tAuthMethod=CHAP\\n'}}]},",
      "  {'type':'result','status':'success','session_id':'choice-delta-process-e2e','result':'## 结论\\n已基于源码证据整理 iSCSI 登录黑盒测试。\\n\\n## 代码证据\\n- `lib/iscsi/iscsi.c`: 登录状态机与 CHAP 参数协商。\\n- `test/iscsi_tgt`: 登录场景可映射到现有测试目录。\\n\\n## 黑盒测试用例\\n1. TC-01 正常登录：前置 target 已启动；步骤发起 Login；预期进入 Full Feature Phase。\\n2. TC-02 CHAP 失败：前置开启 CHAP；步骤使用错误 secret；预期 Login Response 指示认证失败并记录日志。\\n'}",
      "]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "    time.sleep(0.04)",
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
      "answer = '## 结论\\n已生成结构化产物，最终 result 事件作为唯一正文来源。\\n\\n## 代码证据\\n- `lib/iscsi/iscsi.c`: 登录状态机源码文件用于约束测试范围。\\n- `test/iscsi_tgt`: 可映射黑盒登录回归。\\n\\n## 流程梳理\\n1. Agent 先执行源码查找。\\n2. 工具输出进入折叠过程。\\n3. result 字段产出最终测试设计。\\n\\n## 黑盒测试用例\\n' + ''.join([f'{index}. TC-{index:02d} Result 登录场景：前置条件 target 已启动，步骤执行 iSCSI Login 场景 {index}，预期结果可观测，观测点为 Login Response、session 状态和日志。\\n' for index in range(1, 9)])",
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

async function createArtifactHistoryRuntime(
  request: APIRequestContext,
  label: string,
): Promise<{ id: string; name: string; captureFile: string }> {
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-artifact-history-")));
  const runtimeScript = path.join(runtimeDir, "artifact_history_agent.py");
  const captureFile = path.join(runtimeDir, "artifact_history_invocations.jsonl");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, os, pathlib, sys",
      `capture = pathlib.Path(${JSON.stringify(captureFile)})`,
      "prompt = sys.stdin.read()",
      "previous = sum(1 for _ in capture.open(encoding='utf-8')) if capture.exists() else 0",
      "capture.write_text((capture.read_text(encoding='utf-8') if capture.exists() else '') + json.dumps({'turn': previous + 1, 'prompt': prompt}, ensure_ascii=False) + '\\n', encoding='utf-8')",
      "if previous == 0:",
      "    artifact_dir = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
      "    artifact_dir.mkdir(parents=True, exist_ok=True)",
      "    artifact = '\\n'.join([",
      "        '# 第一轮完整产物',",
      "        '',",
      "        '## 黑盒测试用例',",
      "        '1. 用例：正常登录；前置条件：target 已启动；步骤：initiator 发起 Login；预期结果：进入 Full Feature Phase；观测点：Login Response、session 状态和日志。',",
      "        '2. 用例：CHAP 失败恢复；前置条件：target 开启 CHAP；步骤：使用错误 secret 失败后改用正确 secret 重连；预期结果：失败可观测且后续重连成功；观测点：Login Response、认证日志和连接状态。',",
      "        'FULL_ARTIFACT_CONTEXT_MARKER：TC-99 CHAP 失败后重连恢复。',",
      "    ])",
      "    (artifact_dir / 'first-turn-blackbox.md').write_text(artifact, encoding='utf-8')",
      "    print('已生成文件：first-turn-blackbox.md', flush=True)",
      "else:",
      "    seen = 'FULL_ARTIFACT_CONTEXT_MARKER' in prompt",
      "    answer = '\\n'.join([",
      "        '## 结论',",
      "        'HISTORY_ARTIFACT_PROMPT_SEEN=' + str(seen) + '：已基于上一轮完整下载产物继续细化 CHAP 失败恢复。',",
      "        '',",
      "        '## 代码证据',",
      "        '- `lib/iscsi/iscsi.c`: 登录路径与 CHAP 参数协商。',",
      "        '- `test/iscsi_tgt`: 可承载 iSCSI 登录黑盒回归。',",
      "        '',",
      "        '## 黑盒测试用例',",
      "        '1. 用例：CHAP 错误 secret 后恢复；前置条件：target 开启 CHAP；步骤：先使用错误 secret 登录，再使用正确 secret 重连；预期结果：首次失败可观测，第二次进入 Full Feature Phase；观测点：Login Response、认证日志和 session 状态。',",
      "        '2. 用例：CHAP 恢复并发；前置条件：两个 initiator 并发登录；步骤：一个 initiator 使用错误 secret，另一个使用正确 secret；预期结果：失败不影响成功路径；观测点：连接数、错误日志和目标状态。',",
      "    ])",
      "    print(answer, flush=True)",
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
      timeout_seconds: 20,
      enabled: true,
      completion_mode: "process_exit",
      session_persistence: "none",
    },
  });
  expect(runtimeResp.status()).toBe(201);
  const runtime = (await runtimeResp.json()) as { id: string };
  return { id: runtime.id, name: runtimeName, captureFile };
}

async function createResumeArtifactHistoryRuntime(
  request: APIRequestContext,
  label: string,
): Promise<{ id: string; name: string; captureFile: string }> {
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-resume-artifact-history-")));
  const runtimeScript = path.join(runtimeDir, "resume_artifact_history_agent.py");
  const captureFile = path.join(runtimeDir, "resume_artifact_history_invocations.jsonl");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, os, pathlib, sys, time",
      `capture = pathlib.Path(${JSON.stringify(captureFile)})`,
      "args = sys.argv[1:]",
      "prompt = args[args.index('-p') + 1] if '-p' in args else ''",
      "prompt_file = pathlib.Path(os.environ['CODETALK_AGENT_PROMPT_FILE']).read_text(encoding='utf-8')",
      "previous = sum(1 for _ in capture.open(encoding='utf-8')) if capture.exists() else 0",
      "capture.write_text((capture.read_text(encoding='utf-8') if capture.exists() else '') + json.dumps({'turn': previous + 1, 'argv': args, 'prompt': prompt, 'prompt_file': prompt_file}, ensure_ascii=False) + '\\n', encoding='utf-8')",
      "resume = args[args.index('--resume') + 1] if '--resume' in args else ''",
      "session_id = 'resume-artifact-e2e-second' if resume else 'resume-artifact-e2e-first'",
      "if previous == 0:",
      "    artifact_dir = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
      "    artifact_dir.mkdir(parents=True, exist_ok=True)",
      "    artifact = '\\n'.join([",
      "        '# 第一轮完整产物',",
      "        '',",
      "        '## 黑盒测试用例',",
      "        'FULL_ARTIFACT_CONTEXT_MARKER：TC-99 CHAP 失败后重连恢复。',",
      "    ])",
      "    (artifact_dir / 'first-turn-blackbox.md').write_text(artifact, encoding='utf-8')",
      "    answer = '已生成文件：first-turn-blackbox.md'",
      "else:",
      "    seen = 'FULL_ARTIFACT_CONTEXT_MARKER' in prompt or 'FULL_ARTIFACT_CONTEXT_MARKER' in prompt_file",
      "    answer = '\\n'.join([",
      "        '## 结论',",
      "        'RESUME_HISTORY_ARTIFACT_PROMPT_SEEN=' + str(seen) + '：已通过 --resume 续接 CLI session，不重复注入上一轮完整产物。',",
      "        '',",
      "        '## 代码证据',",
      "        '- `lib/iscsi/iscsi.c`: 登录路径与 CHAP 参数协商。',",
      "        '',",
      "        '## 黑盒测试用例',",
      "        '1. 用例：CHAP 错误 secret 后恢复；前置条件：target 开启 CHAP；步骤：先失败后重连；预期结果：首次失败可观测，第二次进入 Full Feature Phase。',",
      "    ])",
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
      "answer = ('CODEX_STDIN_REPLY prompt_transport_ok=true resumed:' + resume) if resume else 'CODEX_STDIN_REPLY prompt_transport_ok=true fresh'",
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

async function createCodexExitOneRuntime(
  request: APIRequestContext,
  label: string,
): Promise<{ id: string; name: string; captureFile: string }> {
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-codex-exit-one-")));
  const runtimeScript = path.join(runtimeDir, "fake_codex_exit_one_agent.py");
  const captureFile = path.join(runtimeDir, "codex_exit_one_invocations.jsonl");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, pathlib, sys, time",
      `capture = pathlib.Path(${JSON.stringify(captureFile)})`,
      "args = sys.argv[1:]",
      "stdin = sys.stdin.read()",
      "capture.write_text((capture.read_text(encoding='utf-8') if capture.exists() else '') + json.dumps({'argv': args, 'stdin': stdin}, ensure_ascii=False) + '\\n', encoding='utf-8')",
      "print(json.dumps({'type':'thread.started','thread_id':'codex-exit-one-e2e'}, ensure_ascii=False), flush=True)",
      "time.sleep(0.05)",
      "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'CODEX_EXIT_ONE_E2E_FINAL 已基于源码完成分析。'}}, ensure_ascii=False), flush=True)",
      "print('Codex CLI exited with code 1 after final answer', file=sys.stderr, flush=True)",
      "raise SystemExit(1)",
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

async function createStructuredCodexCaptureRuntime(
  request: APIRequestContext,
  label: string,
): Promise<{ id: string; name: string; captureFile: string }> {
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-codex-structured-")));
  const runtimeScript = path.join(runtimeDir, "fake_structured_codex_agent.py");
  const captureFile = path.join(runtimeDir, "codex_invocations.jsonl");
  const answer = [
    "## 结论",
    "MULTILINE_PROMPT_CAPTURE_OK：已基于 `lib/iscsi/iscsi.c` 输出 iSCSI login 分析。",
    "",
    "## 代码证据",
    "- `lib/iscsi/iscsi.c`: login PDU 处理与阶段推进入口。",
    "- `test/iscsi_tgt`: 可承载 login、CHAP、digest 的端到端测试。",
    "",
    "## 流程梳理",
    "1. initiator 发起 Login Request。",
    "2. target 校验参数、认证信息和协商选项。",
    "3. 成功时进入 Full Feature Phase，失败时返回可观测 Login Response。",
    "",
    "## SFMEA",
    "| failure mode | cause | effect | severity | occurrence | detection | RPN | mitigation |",
    "| login 参数越界 | 协商字段非法 | login 被拒绝或 session 异常 | 8 | 3 | 4 | 96 | 增加非法参数与日志观测测试 |",
    "",
    "## 黑盒测试用例",
    "1. 用例：合法 login 成功；前置条件：target 已启动；步骤：initiator 发起合法 login；预期结果：进入 Full Feature Phase；观测点：状态、日志、连接数。",
    "2. 用例：非法参数 login 失败；前置条件：target 已启动；步骤：提交越界参数；预期结果：返回失败状态且不中断其它 session；观测点：Login Response 与错误日志。",
  ].join("\n");
  fs.writeFileSync(
    runtimeScript,
    [
      "# -*- coding: utf-8 -*-",
      "import json, pathlib, sys, time",
      `capture = pathlib.Path(${JSON.stringify(captureFile)})`,
      `answer = ${JSON.stringify(answer)}`,
      "args = sys.argv[1:]",
      "stdin = sys.stdin.read()",
      "capture.write_text((capture.read_text(encoding='utf-8') if capture.exists() else '') + json.dumps({'argv': args, 'stdin': stdin}, ensure_ascii=False) + '\\n', encoding='utf-8')",
      "print(json.dumps({'type':'thread.started','thread_id':'codex-structured-capture'}, ensure_ascii=False), flush=True)",
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
      session_persistence: "none",
    },
  });
  expect(runtimeResp.status()).toBe(201);
  const runtime = (await runtimeResp.json()) as { id: string };
  return { id: runtime.id, name: runtimeName, captureFile };
}

async function createDiagnosticOnlySourceRuntime(
  request: APIRequestContext,
  label: string,
): Promise<{ id: string; name: string; captureFile: string }> {
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-diagnostic-only-")));
  const runtimeScript = path.join(runtimeDir, "diagnostic_only_agent.py");
  const captureFile = path.join(runtimeDir, "diagnostic_only_invocations.jsonl");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, pathlib, sys",
      `capture = pathlib.Path(${JSON.stringify(captureFile)})`,
      "prompt = sys.stdin.read()",
      "capture.write_text((capture.read_text(encoding='utf-8') if capture.exists() else '') + json.dumps({'prompt': prompt}, ensure_ascii=False) + '\\n', encoding='utf-8')",
      "print('TOOL: rg nvmf_ctrlr_connect lib/nvmf/ctrlr.c', flush=True)",
      "print('lib/nvmf/ctrlr.c:1:int nvmf_ctrlr_connect(void) { return 0; }', flush=True)",
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
      timeout_seconds: 20,
      enabled: true,
      completion_mode: "process_exit",
      session_persistence: "none",
    },
  });
  expect(runtimeResp.status()).toBe(201);
  const runtime = (await runtimeResp.json()) as { id: string };
  return { id: runtime.id, name: runtimeName, captureFile };
}

async function createOneLineSourceRepairRuntime(
  request: APIRequestContext,
  label: string,
): Promise<{ id: string; name: string; captureFile: string }> {
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-one-line-source-")));
  const runtimeScript = path.join(runtimeDir, "one_line_source_agent.py");
  const captureFile = path.join(runtimeDir, "one_line_source_invocations.jsonl");
  const answer = [
    "## 结论",
    "ONE_LINE_SOURCE_REPAIRED：已基于 `lib/nvmf/ctrlr.c` 总结 connect 入口。",
    "",
    "## 代码证据",
    "- `lib/nvmf/ctrlr.c`: `nvmf_ctrlr_connect` 是本轮入口候选。",
    "",
    "## 行为总结",
    "1. 外部连接请求进入 target connect 处理。",
    "2. 入口负责校验连接上下文并进入控制器建立路径。",
  ].join("\n");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, pathlib, sys",
      `capture = pathlib.Path(${JSON.stringify(captureFile)})`,
      `answer = ${JSON.stringify(answer)}`,
      "prompt = sys.stdin.read()",
      "previous = sum(1 for _ in capture.open(encoding='utf-8')) if capture.exists() else 0",
      "capture.write_text((capture.read_text(encoding='utf-8') if capture.exists() else '') + json.dumps({'turn': previous + 1, 'prompt': prompt}, ensure_ascii=False) + '\\n', encoding='utf-8')",
      "if previous == 0:",
      "    print('最终答案：已完成源码分析。', flush=True)",
      "else:",
      "    print(answer, flush=True)",
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
      timeout_seconds: 20,
      enabled: true,
      completion_mode: "process_exit",
      session_persistence: "none",
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
      "marker = ('resumed claude:' + resume) if resume else 'fresh claude print'",
      "answer = '## 结论\\n' + marker + '\\n\\n## 代码证据\\n- `README.md`: Claude resume transport e2e workspace 来自当前工作区。\\n- `lib/iscsi/iscsi.c`: 作为本轮源码分析的候选证据路径。\\n\\n## 流程梳理\\n1. CodeTalk 通过 Claude print 参数启动本地 Agent。\\n2. 第二轮通过 --resume 续接上一轮 CLI session。'",
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
      "marker = ('resumed opencode:' + session) if session else 'fresh opencode run'",
      "answer = '## 结论\\n' + marker + '\\n\\n## 代码证据\\n- `README.md`: OpenCode resume transport e2e workspace 来自当前工作区。\\n- `lib/iscsi/iscsi.c`: 作为本轮源码分析的候选证据路径。\\n\\n## 流程梳理\\n1. CodeTalk 通过 OpenCode run 启动本地 Agent。\\n2. 第二轮通过 --session 续接上一轮 CLI session。'",
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

test("custom agent created from settings waits for process exit by default", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-custom-defaults-")));
  fs.writeFileSync(path.join(repo, "README.md"), "custom default idle workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-custom-defaults-")));
  const runtimeScript = path.join(runtimeDir, "custom_idle_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import os, sys, time",
      "prompt = sys.stdin.read()",
      "prompt_file = os.environ.get('CODETALK_AGENT_PROMPT_FILE')",
      "file_prompt = open(prompt_file, encoding='utf-8').read() if prompt_file else ''",
      "assert '分析工作区源码' in prompt or '分析工作区源码' in file_prompt",
      "print('thinking: 默认自定义 Agent 正在读取 README.md', flush=True)",
      "time.sleep(1.4)",
      "print('\\n'.join([",
      "  '## 结论',",
      "  '默认自定义 Agent 已完成源码分析，并且等待进程自然退出。',",
      "  '## 代码证据',",
      "  '- `README.md`: 工作区入口文件，证明 Agent 以项目目录为上下文读取源码材料。',",
      "  '- `lib/example.c`: 示例源码证据占位，用于满足源码分析答案的文件引用结构。',",
      "  '## 流程',",
      "  '1. 接收 AI 线程中的用户任务。',",
      "  '2. 读取 stdin 与 CODETALK_AGENT_PROMPT_FILE 中的完整 prompt。',",
      "  '3. 先输出过程诊断，随后输出最终分析并退出。',",
      "]), flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-custom-defaults-${Date.now()}`;
  const threadTitle = `${workspaceName} default idle`;
  const runtimeName = `UI custom idle runtime ${Date.now()}`;

  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);

  let runtimeId = "";
  try {
    await page.goto("/settings", { waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: /自定义命令/ }).hover();
    await page.getByRole("button", { name: /自定义命令/ }).click();
    await page.getByPlaceholder("例如 Claude Code").fill(runtimeName);
    await page.getByPlaceholder("ccr / opencode / nga").fill("python3");
    await page.getByPlaceholder("code 或 run").fill(runtimeScript);
    await page.getByRole("button", { name: "保存" }).hover();
    await page.getByRole("button", { name: "保存" }).click();

    const savedRuntime = page
      .locator("div.rounded-xl.border")
      .filter({ has: page.locator("strong", { hasText: runtimeName }) })
      .filter({ hasText: "python3" })
      .first();
    await expect(savedRuntime).toBeVisible({ timeout: 15_000 });

    const runtimesResp = await request.get(`${backendBase}/api/settings/agent-runtimes`);
    expect(runtimesResp.ok()).toBeTruthy();
    const runtimes = (await runtimesResp.json()) as {
      items: Array<{
        id: string;
        name: string;
        output_mode: string;
        completion_mode: string;
        timeout_seconds: number;
      }>;
    };
    const runtime = runtimes.items.find((item) => item.name === runtimeName);
    expect(runtime).toBeTruthy();
    runtimeId = runtime?.id ?? "";
    expect(runtime?.output_mode).toBe("auto");
    expect(runtime?.completion_mode).toBe("process_exit");
    expect(runtime?.timeout_seconds).toBe(900);

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

    const composer = page.getByLabel("AI 线程消息");
    await composer.click();
    await composer.pressSequentially("分析工作区源码，并给出一句最终答案");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    await expect(
      page.locator(".ct-codex-message").filter({ hasText: "默认自定义 Agent 已完成源码分析" }),
    ).toBeVisible({ timeout: 30_000 });
    await expect
      .poll(async () => {
        const resp = await request.get(`${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}`);
        if (!resp.ok()) return "missing";
        const body = (await resp.json()) as { status?: string; latest_run?: { status?: string } | null };
        return `${body.status ?? ""}:${body.latest_run?.status ?? ""}`;
      }, { timeout: 15_000 })
      .toBe("idle:completed");
  } finally {
    if (runtimeId) {
      await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtimeId)}`);
    }
  }
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
    await expect(page.getByText("生成诊断：默认折叠")).toHaveCount(0);

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

test("keeps Claude delta process text without block start out of visible answer", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-claude-no-start-")));
  fs.mkdirSync(path.join(repo, "lib", "iscsi"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "iscsi", "iscsi.c"),
    "int iscsi_conn_login_pdu_success_complete(void *arg) { return 0; }\n",
    "utf8",
  );
  const workspaceName = `ai-claude-no-start-e2e-${Date.now()}`;
  const threadTitle = `${workspaceName} process cleanup`;

  const runtime = await createClaudeDeltaWithoutBlockStartRuntime(request, "Claude delta no-start runtime");
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
    await composer.pressSequentially("针对 iSCSI 登录写几个黑盒测试用例");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const answer = page.locator(".ct-codex-message").filter({ hasText: "TC-02 CHAP 失败" });
    await expect(answer).toBeVisible({ timeout: 30_000 });
    await expect(page.locator(".ct-codex-message").filter({ hasText: "THINKING" })).toHaveCount(0);
    await expect(page.locator(".ct-codex-message").filter({ hasText: "我先核对工作区" })).toHaveCount(0);
    await expect(page.locator(".ct-codex-message").filter({ hasText: "Bash" })).toHaveCount(0);
    await expect(page.locator(".ct-codex-message").filter({ hasText: "iscsi_conn_login_pdu_success_complete" })).toHaveCount(0);
    await expect(page.locator(".ct-codex-message").filter({ hasText: "AuthMethod=CHAP" })).toHaveCount(0);

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect(processDisclosure.getByText(/默认折叠/)).toBeVisible();
    await expect
      .poll(async () => processDisclosure.evaluate((node) => (node as HTMLDetailsElement).open))
      .toBe(false);
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("我先核对工作区 iSCSI 登录相关源码").first()).toBeVisible();
    await expect(processDisclosure.getByText("iscsi_conn_login_pdu_success_complete").first()).toBeVisible();

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("TC-02 CHAP 失败");
    expect(assistant?.content).not.toContain("THINKING");
    expect(assistant?.content).not.toContain("我先核对工作区");
    expect(assistant?.content).not.toContain("Bash");
    expect(assistant?.content).not.toContain("iscsi_conn_login_pdu_success_complete");
    expect(assistant?.content).not.toContain("AuthMethod=CHAP");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("keeps choice-delta process text out of the visible agent answer", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-choice-delta-")));
  fs.mkdirSync(path.join(repo, "lib", "iscsi"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "iscsi", "iscsi.c"),
    "int iscsi_conn_login_pdu_success_complete(void *arg) { return 0; }\n",
    "utf8",
  );
  const workspaceName = `ai-choice-delta-e2e-${Date.now()}`;
  const threadTitle = `${workspaceName} cleanup`;

  const runtime = await createChoiceDeltaProcessRuntime(request, "Choice delta cleanup runtime");
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
    await composer.pressSequentially("针对 iSCSI 登录写几个黑盒测试用例");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const answer = page.locator(".ct-codex-message").filter({ hasText: "TC-02 CHAP 失败" });
    await expect(answer).toBeVisible({ timeout: 30_000 });
    await expect(page.locator(".ct-codex-message").filter({ hasText: "THINKING" })).toHaveCount(0);
    await expect(page.locator(".ct-codex-message").filter({ hasText: "我先核对工作区" })).toHaveCount(0);
    await expect(page.locator(".ct-codex-message").filter({ hasText: "Bash" })).toHaveCount(0);
    await expect(page.locator(".ct-codex-message").filter({ hasText: "iscsi_conn_login_pdu_success_complete" })).toHaveCount(0);

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect(processDisclosure.getByText(/默认折叠/)).toBeVisible();
    await expect
      .poll(async () => processDisclosure.evaluate((node) => (node as HTMLDetailsElement).open))
      .toBe(false);
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("GitNexus/CGC 图谱产物未命中").first()).toBeVisible();
    await expect(processDisclosure.getByText("降级读取工作区源码").first()).toBeVisible();
    await expect(processDisclosure.getByText("我先核对工作区 iSCSI 登录相关源码").first()).toBeVisible();
    await expect(processDisclosure.getByText("iscsi_conn_login_pdu_success_complete").first()).toBeVisible();

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("TC-02 CHAP 失败");
    expect(assistant?.content).not.toContain("THINKING");
    expect(assistant?.content).not.toContain("我先核对工作区");
    expect(assistant?.content).not.toContain("Bash");
    expect(assistant?.content).not.toContain("iscsi_conn_login_pdu_success_complete");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("keeps plain parenthesized tool invocations out of the visible agent answer", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-plain-tool-call-")));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "nvmf", "ctrlr.c"),
    "int nvmf_ctrlr_plain_tool_probe(void) { return 0; }\n",
    "utf8",
  );
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-plain-tool-call-")));
  const runtimeScript = path.join(runtimeDir, "plain_tool_call_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "sys.stdin.read()",
      "print(\"Read(file_path='lib/nvmf/ctrlr.c')\", flush=True)",
      "print(\"Bash(command='rg nvmf_ctrlr_plain_tool_probe lib/nvmf')\", flush=True)",
      "print('lib/nvmf/ctrlr.c:1:int nvmf_ctrlr_plain_tool_probe(void) { return 0; }', flush=True)",
      "print('## 结论\\nPAREN_TOOL_FINAL: 已完成源码分析，工具调用仅进入折叠过程。\\n\\n## 代码证据\\n- `lib/nvmf/ctrlr.c`: `nvmf_ctrlr_plain_tool_probe` 是当前工作区源码证据。\\n- `test/nvmf`: 可承载 connect/reconnect 黑盒回归。\\n\\n## 黑盒测试用例\\n### TC-01 正常连接\\n前置条件：target 已启动；步骤：initiator 发起 connect；预期结果：连接成功；观测点：RPC 状态、连接状态和 target 日志；失败诊断线索：若连接失败，检查 listener、NQN 和 target 日志。\\n### TC-02 连接超时\\n前置条件：注入网络延迟；步骤：发起 connect 并等待超时；预期结果：返回超时错误且可重连；观测点：错误码、重试状态和 target 日志；失败诊断线索：若未超时，检查延迟注入和重试参数。', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-plain-tool-e2e-${Date.now()}`;
  const runtimeName = `Plain tool call runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} folded plain tools`;

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
      timeout_seconds: 20,
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

    await page.getByLabel("AI 线程消息").fill("基于源码输出两个黑盒测试用例");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const answer = page.locator(".ct-codex-message").filter({ hasText: "PAREN_TOOL_FINAL" });
    await expect(answer).toBeVisible({ timeout: 30_000 });
    await expect(answer).not.toContainText("Read(file_path");
    await expect(answer).not.toContainText("Bash(command");
    await expect(answer).not.toContainText("nvmf_ctrlr_plain_tool_probe(void");

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect(processDisclosure.getByText(/默认折叠/)).toBeVisible();
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("Read(file_path='lib/nvmf/ctrlr.c')")).toBeVisible();
    await expect(processDisclosure.getByText("Bash(command='rg nvmf_ctrlr_plain_tool_probe lib/nvmf')")).toBeVisible();
    await expect(processDisclosure.getByText("nvmf_ctrlr_plain_tool_probe(void")).toBeVisible();

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("PAREN_TOOL_FINAL");
    expect(assistant?.content).not.toContain("Read(file_path");
    expect(assistant?.content).not.toContain("Bash(command");
    expect(assistant?.content).not.toContain("nvmf_ctrlr_plain_tool_probe(void");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("folds plain shell transcript output into Agent process instead of the answer", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-shell-transcript-")));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "nvmf", "ctrlr.c"),
    "int nvmf_ctrlr_shell_probe(void) { return 0; }\n",
    "utf8",
  );
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-shell-transcript-")));
  const runtimeScript = path.join(runtimeDir, "shell_transcript_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "sys.stdin.read()",
      "print('$ rg nvmf_ctrlr_shell_probe lib/nvmf', flush=True)",
      "print('lib/nvmf/ctrlr.c:1:int nvmf_ctrlr_shell_probe(void) { return 0; }', flush=True)",
      "print('exit_code=0', flush=True)",
      "print('## 结论\\nSHELL_TRANSCRIPT_FINAL: 已完成源码分析，shell transcript 仅进入折叠过程。\\n\\n## 代码证据\\n- `lib/nvmf/ctrlr.c`: `nvmf_ctrlr_shell_probe` 是当前工作区源码证据。\\n- `test/nvmf`: 可承载 connect/reconnect 黑盒回归。\\n\\n## 黑盒测试用例\\n### TC-01 正常连接\\n前置条件：target 已启动；步骤：initiator 发起 connect；预期结果：连接成功；观测点：RPC 状态、连接状态和 target 日志。\\n### TC-02 连接超时\\n前置条件：注入网络延迟；步骤：发起 connect 并等待超时；预期结果：返回超时错误且可重连；观测点：错误码、重试状态和 target 日志。', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-shell-transcript-e2e-${Date.now()}`;
  const runtimeName = `Shell transcript runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} folded shell transcript`;

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
      timeout_seconds: 20,
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

    await page.getByLabel("AI 线程消息").fill("基于源码输出两个黑盒测试用例");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const answer = page.locator(".ct-codex-message").filter({ hasText: "SHELL_TRANSCRIPT_FINAL" });
    await expect(answer).toBeVisible({ timeout: 30_000 });
    await expect(answer).not.toContainText("$ rg nvmf_ctrlr_shell_probe");
    await expect(answer).not.toContainText("nvmf_ctrlr_shell_probe(void");
    await expect(answer).not.toContainText("exit_code=0");

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect(processDisclosure.getByText(/默认折叠/)).toBeVisible();
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("$ rg nvmf_ctrlr_shell_probe lib/nvmf")).toBeVisible();
    await expect(processDisclosure.getByText("nvmf_ctrlr_shell_probe(void")).toBeVisible();
    await expect(processDisclosure.getByText("exit_code=0")).toBeVisible();

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("SHELL_TRANSCRIPT_FINAL");
    expect(assistant?.content).not.toContain("$ rg nvmf_ctrlr_shell_probe");
    expect(assistant?.content).not.toContain("nvmf_ctrlr_shell_probe(void");
    expect(assistant?.content).not.toContain("exit_code=0");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("strips final-answer wrappers from real agent output before display and persistence", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-final-wrapper-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI final wrapper cleanup e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-final-wrapper-")));
  const runtimeScript = path.join(runtimeDir, "final_wrapper_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "sys.stdin.read()",
      "print('FINAL ANSWER:', flush=True)",
      "print('## 结论', flush=True)",
      "print('WRAPPED_FINAL_MARKER_CLEAN: 已完成源码分析，协议包装不会展示给用户。', flush=True)",
      "print('', flush=True)",
      "print('## 代码证据', flush=True)",
      "print('- `README.md`: `AI final wrapper cleanup e2e workspace` 来自当前工作区。', flush=True)",
      "print('- `test/nvmf`: 可承载 connect/reconnect 黑盒回归。', flush=True)",
      "print('', flush=True)",
      "print('## 黑盒测试用例', flush=True)",
      "print('### TC-01 正常连接', flush=True)",
      "print('前置条件：target 已启动；步骤：initiator 发起 connect；预期结果：连接成功；观测点：RPC 状态、连接状态和 target 日志。', flush=True)",
      "print('最终答案：中文包装词也应该被剥离。', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-final-wrapper-e2e-${Date.now()}`;
  const runtimeName = `Final wrapper runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} final wrapper`;

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
      timeout_seconds: 20,
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

    await page.getByLabel("AI 线程消息").fill("请输出最终答案，不要展示协议包装词");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const answer = page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "WRAPPED_FINAL_MARKER_CLEAN" });
    await expect(answer).toBeVisible({ timeout: 30_000 });
    await expect(answer).not.toContainText("FINAL ANSWER");
    await expect(answer).not.toContainText("最终答案：");
    await expect(answer).toContainText("中文包装词也应该被剥离。");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("WRAPPED_FINAL_MARKER_CLEAN");
    expect(assistant?.content).toContain("中文包装词也应该被剥离。");
    expect(assistant?.content).not.toContain("FINAL ANSWER");
    expect(assistant?.content).not.toContain("最终答案：");

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出" }).hover();
    await page.getByRole("button", { name: "导出" }).click();
    const download = await downloadPromise;
    const exportPath = testInfo.outputPath("final-wrapper-clean-export.md");
    await download.saveAs(exportPath);
    const exported = fs.readFileSync(exportPath, "utf8");
    expect(exported).toContain("WRAPPED_FINAL_MARKER_CLEAN");
    expect(exported).toContain("中文包装词也应该被剥离。");
    expect(exported).not.toContain("FINAL ANSWER");
    expect(exported).not.toContain("最终答案：");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("carries a previous downloadable agent artifact into the next non-resume prompt", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-artifact-history-")));
  fs.mkdirSync(path.join(repo, "lib", "iscsi"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "iscsi", "iscsi.c"),
    "int iscsi_conn_login_pdu_success_complete(void *arg) { return 0; }\n",
    "utf8",
  );
  const workspaceName = `ai-artifact-history-e2e-${Date.now()}`;
  const threadTitle = `${workspaceName} continuity`;

  const runtime = await createArtifactHistoryRuntime(request, "Artifact history runtime");
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
    await composer.pressSequentially("生成完整 iSCSI 登录黑盒测试用例文件");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.getByRole("link", { name: "下载完整产物" })).toBeVisible({ timeout: 30_000 });
    await expect(page.locator(".ct-codex-message").filter({ hasText: "FULL_ARTIFACT_CONTEXT_MARKER" })).toHaveCount(0);

    await composer.click();
    await composer.pressSequentially("基于上一轮继续细化 CHAP 失败恢复场景");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.locator(".ct-codex-message").filter({ hasText: "HISTORY_ARTIFACT_PROMPT_SEEN=True" })).toBeVisible({
      timeout: 30_000,
    });

    const captured = fs.readFileSync(runtime.captureFile, "utf8").trim().split("\n").map((line) => JSON.parse(line)) as Array<{
      turn: number;
      prompt: string;
    }>;
    expect(captured).toHaveLength(2);
    expect(captured[0].prompt).not.toContain("FULL_ARTIFACT_CONTEXT_MARKER");
    expect(captured[1].prompt).toContain("历史助手完整下载产物");
    expect(captured[1].prompt).toContain("FULL_ARTIFACT_CONTEXT_MARKER");
    expect(captured[1].prompt).toContain("TC-99 CHAP 失败后重连恢复");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    expect(messageBody.items.filter((item) => item.role === "user")).toHaveLength(2);
    expect(messageBody.items.at(-1)?.content).toContain("HISTORY_ARTIFACT_PROMPT_SEEN=True");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("skips previous downloadable artifact injection when the agent runtime resumes a CLI session", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-resume-artifact-history-")));
  fs.mkdirSync(path.join(repo, "lib", "iscsi"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "iscsi", "iscsi.c"),
    "int iscsi_conn_login_pdu_success_complete(void *arg) { return 0; }\n",
    "utf8",
  );
  const workspaceName = `ai-resume-artifact-history-e2e-${Date.now()}`;
  const threadTitle = `${workspaceName} continuity`;

  const runtime = await createResumeArtifactHistoryRuntime(request, "Resume artifact history runtime");
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
    await composer.pressSequentially("生成完整 iSCSI 登录黑盒测试用例文件");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.getByRole("link", { name: "下载完整产物" })).toBeVisible({ timeout: 30_000 });
    await expect(page.locator(".ct-codex-message").filter({ hasText: "FULL_ARTIFACT_CONTEXT_MARKER" })).toHaveCount(0);

    await composer.click();
    await composer.pressSequentially("基于上一轮继续细化 CHAP 失败恢复场景");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.locator(".ct-codex-message").filter({ hasText: "RESUME_HISTORY_ARTIFACT_PROMPT_SEEN=False" })).toBeVisible({
      timeout: 30_000,
    });

    const captured = fs.readFileSync(runtime.captureFile, "utf8").trim().split("\n").map((line) => JSON.parse(line)) as Array<{
      turn: number;
      argv: string[];
      prompt: string;
      prompt_file: string;
    }>;
    expect(captured).toHaveLength(2);
    expect(captured[0].argv).not.toContain("--resume");
    expect(captured[0].prompt).not.toContain("FULL_ARTIFACT_CONTEXT_MARKER");
    expect(captured[0].prompt_file).not.toContain("FULL_ARTIFACT_CONTEXT_MARKER");
    expect(captured[1].argv).toEqual(expect.arrayContaining(["--resume", "resume-artifact-e2e-first", "-p"]));
    expect(captured[1].prompt).toContain("基于上一轮继续细化 CHAP 失败恢复场景");
    expect(captured[1].prompt_file).toContain("基于上一轮继续细化 CHAP 失败恢复场景");
    expect(captured[1].prompt).not.toContain("历史助手完整下载产物");
    expect(captured[1].prompt).not.toContain("FULL_ARTIFACT_CONTEXT_MARKER");
    expect(captured[1].prompt_file).not.toContain("历史助手完整下载产物");
    expect(captured[1].prompt_file).not.toContain("FULL_ARTIFACT_CONTEXT_MARKER");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    expect(messageBody.items.filter((item) => item.role === "user")).toHaveLength(2);
    expect(messageBody.items.at(-1)?.content).toContain("RESUME_HISTORY_ARTIFACT_PROMPT_SEEN=False");
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
    const threadId = page.url().split("/").pop() ?? "";
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

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("iscsi_conn_login_pdu_success_complete").first()).toBeVisible();
    await expect(page.getByText("生成诊断：默认折叠")).toHaveCount(0);

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
  const otherRepo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-sibling-other-")));
  fs.writeFileSync(path.join(otherRepo, "README.md"), "AI sibling side rail count fixture\n", "utf8");
  const otherWorkspaceName = `${workspaceName}-other`;

  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);
  const workspace = (await workspaceResp.json()) as { id: string };
  const otherWorkspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: otherWorkspaceName, repo_path: otherRepo },
  });
  expect(otherWorkspaceResp.status()).toBe(201);
  const otherWorkspace = (await otherWorkspaceResp.json()) as { id: string };
  const otherThreadResp = await request.post(`${backendBase}/api/ai/conversations`, {
    data: {
      scope_type: "workspace",
      scope_id: otherWorkspace.id,
      workspace_id: otherWorkspace.id,
      title: `${otherWorkspaceName} count fixture`,
      initial_context: {
        workspace_id: otherWorkspace.id,
        project_name: otherWorkspaceName,
      },
    },
  });
  expect(otherThreadResp.status()).toBe(201);

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
  const detailProjectRow = page.locator(".ct-codex-ai__project-row").filter({ hasText: workspaceName }).first();
  await expect(detailProjectRow.locator("em")).toHaveText("2");
  const projectSearch = page.getByLabel("搜索 AI 项目");
  await projectSearch.fill(otherWorkspaceName);
  const otherProjectRow = page.locator(".ct-codex-ai__project-row").filter({ hasText: otherWorkspaceName }).first();
  await expect(otherProjectRow.locator("em")).toHaveText("1");
  await projectSearch.fill("");

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
  const workspaceIdsToDelete: string[] = [];

  for (let index = 0; index < 12; index += 1) {
    const extraRepo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), `codetalk-ai-list-extra-${index}-`)));
    fs.writeFileSync(path.join(extraRepo, "README.md"), `AI list extra workspace ${index}\n`, "utf8");
    const extraWorkspaceResp = await request.post(`${backendBase}/api/workspaces`, {
      data: { name: `ai-list-extra-${stamp}-${index}`, repo_path: extraRepo },
    });
    expect(extraWorkspaceResp.status()).toBe(201);
    workspaceIdsToDelete.push(((await extraWorkspaceResp.json()) as { id: string }).id);
  }

  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-list-target-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI list containment target workspace\n", "utf8");
  const workspaceName = `ai-list-target-${stamp}`;
  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);
  const workspace = (await workspaceResp.json()) as { id: string };
  workspaceIdsToDelete.push(workspace.id);

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
    const projectsPanel = document.querySelector(".ct-ai-home__projects") as HTMLElement | null;
    const threadsPanel = document.querySelector(".ct-ai-home__threads") as HTMLElement | null;
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
      projectsBackdropFilter: projectsPanel ? window.getComputedStyle(projectsPanel).backdropFilter : "",
      threadsBackdropFilter: threadsPanel ? window.getComputedStyle(threadsPanel).backdropFilter : "",
      threadsAnimationName: threadsPanel ? window.getComputedStyle(threadsPanel).animationName : "",
    };
  });
  expect(homeMetrics.documentScrollHeight).toBeLessThanOrEqual(homeMetrics.viewportHeight + 40);
  expect(homeMetrics.homeHeight).toBeLessThanOrEqual(homeMetrics.viewportHeight);
  expect(homeMetrics.projectScrollHeight).toBeGreaterThan(homeMetrics.projectClientHeight + 120);
  expect(homeMetrics.threadScrollHeight).toBeGreaterThan(homeMetrics.threadClientHeight + 120);
  expect(homeMetrics.projectOverflowY).toBe("auto");
  expect(homeMetrics.threadOverflowY).toBe("auto");
  expect(homeMetrics.windowScrollY).toBe(0);
  expect(homeMetrics.projectsBackdropFilter).toBe("none");
  expect(homeMetrics.threadsBackdropFilter).toBe("none");
  expect(homeMetrics.threadsAnimationName).toBe("none");

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
    const rail = document.querySelector(".ct-codex-ai__rail") as HTMLElement | null;
    const main = document.querySelector(".ct-codex-ai__main") as HTMLElement | null;
    const context = document.querySelector(".ct-codex-ai__context") as HTMLElement | null;
    return {
      documentScrollHeight: document.documentElement.scrollHeight,
      viewportHeight: window.innerHeight,
      shellHeight: shell?.getBoundingClientRect().height ?? 0,
      threadClientHeight: threadList?.clientHeight ?? 0,
      threadScrollHeight: threadList?.scrollHeight ?? 0,
      threadOverflowY: threadList ? window.getComputedStyle(threadList).overflowY : "",
      railBackdropFilter: rail ? window.getComputedStyle(rail).backdropFilter : "",
      mainBackdropFilter: main ? window.getComputedStyle(main).backdropFilter : "",
      contextBackdropFilter: context ? window.getComputedStyle(context).backdropFilter : "",
    };
  });
  expect(threadPageMetrics.documentScrollHeight).toBeLessThanOrEqual(threadPageMetrics.viewportHeight + 40);
  expect(threadPageMetrics.shellHeight).toBeLessThanOrEqual(threadPageMetrics.viewportHeight);
  expect(threadPageMetrics.threadScrollHeight).toBeGreaterThan(threadPageMetrics.threadClientHeight + 120);
  expect(threadPageMetrics.threadOverflowY).toBe("auto");
  expect(threadPageMetrics.railBackdropFilter).toBe("none");
  expect(threadPageMetrics.mainBackdropFilter).toBe("none");
  expect(threadPageMetrics.contextBackdropFilter).toBe("none");

  const sidebarThreadList = page.locator(".ct-codex-ai__thread-list");
  await sidebarThreadList.hover();
  await page.mouse.wheel(0, 1200);
  await expect.poll(() => sidebarThreadList.evaluate((element) => element.scrollTop)).toBeGreaterThan(120);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeLessThan(5);

  for (const workspaceId of workspaceIdsToDelete.reverse()) {
    await request.delete(`${backendBase}/api/workspaces/${encodeURIComponent(workspaceId)}`);
  }
});

test("renders long real AI thread histories without per-message entry animations", async ({
  page,
  request,
}) => {
  test.setTimeout(120_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-long-history-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI long history performance guard workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-long-history-runtime-")));
  const runtimeScript = path.join(runtimeDir, "long_history_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "# -*- coding: utf-8 -*-",
      "import re, sys",
      "prompt = sys.stdin.read()",
      "matches = re.findall(r'LONG_HISTORY_TURN_(\\d+)', prompt)",
      "turn = matches[-1] if matches else 'unknown'",
      "print(f'## 结论\\nLONG_HISTORY_REPLY_{turn}: 已读取当前工作区并保持长历史渲染轻量。\\n\\n## 代码证据\\n- `README.md`: `AI long history performance guard workspace` 作为本轮真实工作区证据。\\n- `test/nvmf`: 可承载 connect/reconnect 长历史分析的黑盒回归。\\n\\n## 流程梳理\\n1. 用户在同一 AI 线程连续追问 SPDK connect/reconnect 场景。\\n2. CodeTalk 将每轮任务交给真实 agent runtime，并把回答追加到线程历史。\\n3. 浏览器重新打开长历史线程时，只在内部 reader 滚动，不给每条消息添加入场动画。\\n\\n## SFMEA\\n| failure mode | cause | effect | severity | occurrence | detection | RPN | mitigation |\\n| 长历史消息逐条动画 | 每条消息渲染时都触发 transform/animation | 页面卡顿、滚动掉帧 | 7 | 4 | 4 | 112 | 用真实 E2E 检查 animationName、transform 和 will-change |\\n\\n## 黑盒测试用例\\n1. 用例：长历史线程打开不卡顿；前置条件：已有 24 轮真实 agent 对话；步骤：打开线程详情页；预期结果：正文消息全部可见且只在 reader 内滚动；观测点：document scrollHeight、reader overflow。\\n2. 用例：消息无逐条动画；前置条件：长历史已渲染；步骤：读取每条消息 computed style；预期结果：animationName 为 none、transform 为 none、未设置 will-change；失败诊断线索为消息 DOM 上的动画样式。', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-long-history-${Date.now()}`;
  const runtimeName = `Long history perf runtime ${Date.now()}`;
  let runtime: { id: string } | null = null;
  let workspace: { id: string } | null = null;
  let conversation: { id: string } | null = null;

  const waitForMessageCount = async (conversationId: string, expected: number) => {
    await expect
      .poll(
        async () => {
          const response = await request.get(
            `${backendBase}/api/ai/conversations/${encodeURIComponent(conversationId)}/messages`,
          );
          expect(response.ok()).toBeTruthy();
          const body = (await response.json()) as { items: Array<{ role: string; content: string }> };
          return body.items.length;
        },
        { timeout: 45_000 },
      )
      .toBe(expected);
  };

  try {
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
        timeout_seconds: 20,
        enabled: true,
        completion_mode: "process_exit",
      },
    });
    expect(runtimeResp.status()).toBe(201);
    runtime = (await runtimeResp.json()) as { id: string };

    const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
      data: { name: workspaceName, repo_path: repo },
    });
    expect(workspaceResp.status()).toBe(201);
    workspace = (await workspaceResp.json()) as { id: string };

    const conversationResp = await request.post(`${backendBase}/api/ai/conversations`, {
      data: {
        scope_type: "workspace",
        scope_id: workspace.id,
        workspace_id: workspace.id,
        memory_namespace: `workspace:${workspace.id}`,
        runtime_type: "agent_runtime",
        agent_runtime_id: runtime.id,
        title: `${workspaceName} long message history`,
        initial_context: {
          workspace_id: workspace.id,
          project_name: workspaceName,
          memory_namespace: `workspace:${workspace.id}`,
        },
      },
    });
    expect(conversationResp.status()).toBe(201);
    conversation = (await conversationResp.json()) as { id: string };

    for (let index = 1; index <= 24; index += 1) {
      const sendResp = await request.post(
        `${backendBase}/api/ai/conversations/${encodeURIComponent(conversation.id)}/messages`,
        {
          data: {
            content: `LONG_HISTORY_TURN_${String(index).padStart(2, "0")} 分析 SPDK connect/reconnect 并保持长历史页面轻量`,
          },
        },
      );
      expect(sendResp.status()).toBe(202);
      await waitForMessageCount(conversation.id, index * 2);
    }

    await page.setViewportSize({ width: 1440, height: 820 });
    await page.goto(`/ai/${conversation.id}`, { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: `${workspaceName} long message history` })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.locator(".ct-codex-message")).toHaveCount(48);
    await expect(page.getByText("LONG_HISTORY_REPLY_24")).toBeVisible({ timeout: 15_000 });

    const metrics = await page.locator(".ct-codex-ai").evaluate((shell) => {
      const reader = document.querySelector(".ct-codex-ai__reader") as HTMLElement | null;
      const messages = Array.from(document.querySelectorAll(".ct-codex-message")) as HTMLElement[];
      const runningAnimations = document
        .getAnimations({ subtree: true })
        .filter((animation) => {
          const target = animation.effect instanceof KeyframeEffect ? animation.effect.target : null;
          return target instanceof Element && target.closest(".ct-codex-ai");
        })
        .map((animation) => {
          const target = animation.effect instanceof KeyframeEffect ? animation.effect.target : null;
          const timing = animation.effect?.getComputedTiming();
          return {
            className: target instanceof HTMLElement ? target.className : "",
            playState: animation.playState,
            iterations: timing?.iterations,
          };
        })
        .filter((animation) => animation.playState !== "finished" && animation.iterations === Infinity);
      return {
        documentScrollHeight: document.documentElement.scrollHeight,
        viewportHeight: window.innerHeight,
        shellHeight: (shell as HTMLElement).getBoundingClientRect().height,
        readerClientHeight: reader?.clientHeight ?? 0,
        readerScrollHeight: reader?.scrollHeight ?? 0,
        readerOverflowY: reader ? window.getComputedStyle(reader).overflowY : "",
        messageMotion: messages.map((message) => {
          const styles = window.getComputedStyle(message);
          return {
            animationName: styles.animationName,
            animationDuration: styles.animationDuration,
            transform: styles.transform,
            willChange: styles.willChange,
          };
        }),
        runningAnimations,
      };
    });

    expect(metrics.documentScrollHeight).toBeLessThanOrEqual(metrics.viewportHeight + 40);
    expect(metrics.shellHeight).toBeLessThanOrEqual(metrics.viewportHeight);
    expect(metrics.readerOverflowY).toBe("auto");
    expect(metrics.readerScrollHeight).toBeGreaterThan(metrics.readerClientHeight + 600);
    expect(metrics.runningAnimations).toEqual([]);
    expect(
      metrics.messageMotion.filter(
        (item) =>
          item.animationName !== "none" ||
          item.transform !== "none" ||
          (item.willChange !== "auto" && item.willChange.trim() !== ""),
      ),
      "long real AI histories should not animate or promote every message on render",
    ).toEqual([]);

    const reader = page.getByLabel("AI 线程对话内容");
    await reader.hover();
    await page.mouse.wheel(0, -1200);
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeLessThan(5);
  } finally {
    if (conversation) {
      await request.delete(`${backendBase}/api/ai/conversations/${encodeURIComponent(conversation.id)}`).catch(() => undefined);
    }
    if (runtime) {
      await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`).catch(() => undefined);
    }
    if (workspace) {
      await request.delete(`${backendBase}/api/workspaces/${encodeURIComponent(workspace.id)}`).catch(() => undefined);
    }
  }
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
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);
    const threadId = page.url().split("/").pop() ?? "";

    const firstPrompt = "第一轮：验证 Codex transport stdin prompt delivery";
    const composer = page.getByPlaceholder(/像 Codex 一样继续追问/);
    await composer.fill(firstPrompt);
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "CODEX_STDIN_REPLY prompt_transport_ok=true fresh" })).toBeVisible({
      timeout: 20_000,
    });

    const secondPrompt = "第二轮：继续沿用上一轮 session，只输出 resume 证据";
    await composer.fill(secondPrompt);
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "CODEX_STDIN_REPLY prompt_transport_ok=true resumed:codex-e2e-first" })).toBeVisible({
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
        expect.objectContaining({ role: "assistant", content: "CODEX_STDIN_REPLY prompt_transport_ok=true fresh" }),
        expect.objectContaining({ role: "assistant", content: "CODEX_STDIN_REPLY prompt_transport_ok=true resumed:codex-e2e-first" }),
      ]),
    );
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
    await request.delete(`${backendBase}/api/workspaces/${encodeURIComponent(workspace.id)}`);
  }
});

test("Codex agent runtime keeps the final answer when the CLI exits 1 after output", async ({
  page,
  request,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-codex-exit-one-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Codex exit-one e2e workspace\n", "utf8");
  const workspaceName = `ai-codex-exit-one-e2e-${Date.now()}`;
  const threadTitle = `${workspaceName} codex exit one`;
  const runtime = await createCodexExitOneRuntime(request, "Codex exit-one runtime");

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

    await page.getByLabel("AI 线程执行器").selectOption({ label: runtime.name });
    await page.getByPlaceholder(/线程名称/).fill(threadTitle);
    await page.getByRole("button", { name: "新建线程" }).hover();
    await page.getByRole("button", { name: "新建线程" }).click();

    await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);

    const prompt = "请读取工作区源码并输出 Codex exit-one 容错验证";
    const composer = page.getByPlaceholder(/像 Codex 一样继续追问/);
    await composer.fill(prompt);
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "CODEX_EXIT_ONE_E2E_FINAL" })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.locator('div[role="alert"]').filter({ hasText: "Codex CLI exited with code 1" })).toHaveCount(0);

    const captured = fs.readFileSync(runtime.captureFile, "utf8")
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line) as { argv: string[]; stdin: string });
    expect(captured).toHaveLength(1);
    expect(captured[0].argv).toContain("exec");
    expect(captured[0].argv).toContain("--json");
    expect(captured[0].stdin).toContain(prompt);

    const conversationResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}`,
    );
    expect(conversationResp.ok()).toBeTruthy();
    const conversation = (await conversationResp.json()) as {
      status: string;
      latest_run?: { status?: string; error?: string | null };
    };
    expect(conversation.status).toBe("idle");
    expect(conversation.latest_run?.status).toBe("completed");
    expect(conversation.latest_run?.error ?? "").toBe("");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    expect(messageBody.items).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          role: "assistant",
          content: "CODEX_EXIT_ONE_E2E_FINAL 已基于源码完成分析。",
        }),
      ]),
    );
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
    await request.delete(`${backendBase}/api/workspaces/${encodeURIComponent(workspace.id)}`);
  }
});

test("agent runtime receives the complete multiline task from the real AI thread composer", async ({
  page,
  request,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-multiline-prompt-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Multiline prompt transport e2e workspace\n", "utf8");
  const workspaceName = `ai-multiline-prompt-${Date.now()}`;
  const threadTitle = `${workspaceName} multiline prompt`;
  const runtime = await createStructuredCodexCaptureRuntime(request, "Multiline prompt runtime");

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

    const multilinePrompt = [
      "基于当前 SPDK 源码，分析 iSCSI login 流程。",
      "",
      "必须输出：代码证据、流程梳理、SFMEA、黑盒测试用例。",
      "1. 先确认涉及的入口文件。",
      "2. 再梳理 login、CHAP、digest、session reset。",
      "- 黑盒用例只能写外部输入、操作、预期输出和观测点。",
      "不要只回复你好；不要在第一行后截断。",
      "MULTILINE_SENTINEL_LAST_LINE_93217",
    ].join("\n");

    const composer = page.getByPlaceholder(/像 Codex 一样继续追问/);
    await composer.fill(multilinePrompt);
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(
      page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "MULTILINE_PROMPT_CAPTURE_OK" }),
    ).toBeVisible({ timeout: 20_000 });

    const captured = fs.readFileSync(runtime.captureFile, "utf8")
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line) as { argv: string[]; stdin: string });
    expect(captured).toHaveLength(1);
    expect(captured[0].argv.join(" ")).not.toContain("MULTILINE_SENTINEL_LAST_LINE_93217");
    for (const line of multilinePrompt.split("\n")) {
      expect(captured[0].stdin).toContain(line);
    }
    expect(captured[0].stdin).toContain("基于当前 SPDK 源码，分析 iSCSI login 流程。\n\n必须输出");
    expect(captured[0].stdin.indexOf("基于当前 SPDK 源码")).toBeLessThan(
      captured[0].stdin.indexOf("1. 先确认涉及的入口文件。"),
    );
    expect(captured[0].stdin.indexOf("1. 先确认涉及的入口文件。")).toBeLessThan(
      captured[0].stdin.indexOf("- 黑盒用例只能写外部输入"),
    );
    expect(captured[0].stdin.indexOf("- 黑盒用例只能写外部输入")).toBeLessThan(
      captured[0].stdin.indexOf("MULTILINE_SENTINEL_LAST_LINE_93217"),
    );

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    expect(messageBody.items).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ role: "user", content: multilinePrompt }),
        expect.objectContaining({ role: "assistant", content: expect.stringContaining("MULTILINE_PROMPT_CAPTURE_OK") }),
      ]),
    );
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
    await request.delete(`${backendBase}/api/workspaces/${encodeURIComponent(workspace.id)}`);
  }
});

test("diagnostic-only source agent fails visibly instead of idling with a fake answer", async ({
  page,
  request,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-diagnostic-only-repo-")));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.writeFileSync(path.join(repo, "lib", "nvmf", "ctrlr.c"), "int nvmf_ctrlr_connect(void) { return 0; }\n", "utf8");
  const workspaceName = `ai-diagnostic-only-${Date.now()}`;
  const threadTitle = `${workspaceName} diagnostic only`;
  const runtime = await createDiagnosticOnlySourceRuntime(request, "Diagnostic only source runtime");

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

    await page.getByLabel("AI 线程消息").fill("请阅读工作区源码，总结 lib/nvmf/ctrlr.c 里的 connect 入口");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.locator("div[role='alert']").filter({ hasText: "Agent 返回内容不足" })).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByRole("button", { name: "重试上一条" })).toBeVisible();
    await expect(page.getByText("执行器没有返回有效内容")).toHaveCount(0);
    await expect(page.locator(".ct-codex-message:not(.is-user)")).toHaveCount(0);

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("rg nvmf_ctrlr_connect lib/nvmf/ctrlr.c").first()).toBeVisible();
    await expect(page.getByText("生成诊断：默认折叠")).toHaveCount(0);

    const captured = fs.readFileSync(runtime.captureFile, "utf8")
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line) as { prompt: string });
    expect(captured).toHaveLength(2);
    expect(captured[1].prompt).toContain("上一次执行器输出过短");

    const conversationResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}`,
    );
    expect(conversationResp.ok()).toBeTruthy();
    const conversation = (await conversationResp.json()) as {
      status: string;
      latest_run: { status: string; error: string | null } | null;
    };
    expect(conversation.status).toBe("error");
    expect(conversation.latest_run?.status).toBe("failed");
    expect(conversation.latest_run?.error).toContain("仍未产出可验收");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    expect(messageBody.items.map((item) => item.role)).toEqual(["user"]);
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
    await request.delete(`${backendBase}/api/workspaces/${encodeURIComponent(workspace.id)}`);
  }
});

test("keeps failed agent stderr visible inside the folded Agent process", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-error-process-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI error process e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-error-process-")));
  const runtimeScript = path.join(runtimeDir, "failing_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys, time",
      "sys.stdin.read()",
      "print('thinking: ERROR_PROCESS_STEP_01 reading workspace source', flush=True)",
      "time.sleep(0.2)",
      "print('fatal diagnostic: ERROR_PROCESS_FATAL_SOURCE_SCAN failed while opening lib/nvmf/ctrlr.c', file=sys.stderr, flush=True)",
      "sys.exit(2)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-error-process-${Date.now()}`;
  const runtimeName = `Error process runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} folded failure`;

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

    await page.getByLabel("AI 线程执行器").selectOption({ label: runtimeName });
    await page.getByPlaceholder(/线程名称/).fill(threadTitle);
    await page.getByRole("button", { name: "新建线程" }).hover();
    await page.getByRole("button", { name: "新建线程" }).click();

    await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);

    await page.getByLabel("AI 线程消息").fill("请读取工作区源码并解释 connect 路径");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.locator("div[role='alert']").filter({ hasText: "ERROR_PROCESS_FATAL_SOURCE_SCAN" })).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.locator(".ct-codex-message:not(.is-user)")).toHaveCount(0);

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await processDisclosure.locator("summary").hover();
    await processDisclosure.locator("summary").click();
    await expect(processDisclosure.getByText("ERROR_PROCESS_STEP_01")).toBeVisible();
    await expect(processDisclosure.getByText("ERROR_PROCESS_FATAL_SOURCE_SCAN")).toBeVisible();
    await expect(page.getByText("生成诊断：默认折叠")).toHaveCount(0);

  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
    await request.delete(`${backendBase}/api/workspaces/${encodeURIComponent(workspace.id)}`);
  }
});

test("one-line source agent answer is repaired before it becomes the visible assistant reply", async ({
  page,
  request,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-one-line-source-repo-")));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.writeFileSync(path.join(repo, "lib", "nvmf", "ctrlr.c"), "int nvmf_ctrlr_connect(void) { return 0; }\n", "utf8");
  const workspaceName = `ai-one-line-source-${Date.now()}`;
  const threadTitle = `${workspaceName} source repair`;
  const runtime = await createOneLineSourceRepairRuntime(request, "One-line source repair runtime");

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

    await page.getByLabel("AI 线程消息").fill("请阅读工作区源码，总结 lib/nvmf/ctrlr.c 里的 connect 入口");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.getByText("ONE_LINE_SOURCE_REPAIRED")).toBeVisible({ timeout: 30_000 });
    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "nvmf_ctrlr_connect" })).toBeVisible();
    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "已完成源码分析。" })).toHaveCount(0);
    await expect(page.locator("div[role='alert']").filter({ hasText: "Agent 返回内容不足" })).toHaveCount(0);

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("上一次执行器输出过短")).toBeVisible();
    await expect(page.getByText("生成诊断：默认折叠")).toHaveCount(0);

    const captured = fs.readFileSync(runtime.captureFile, "utf8")
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line) as { turn: number; prompt: string });
    expect(captured.map((item) => item.turn)).toEqual([1, 2]);
    expect(captured[1].prompt).toContain("不要只说已完成");

    const conversationResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}`,
    );
    expect(conversationResp.ok()).toBeTruthy();
    const conversation = (await conversationResp.json()) as {
      status: string;
      latest_run: { status: string; error: string | null } | null;
    };
    expect(conversation.status).toBe("idle");
    expect(conversation.latest_run?.status).toBe("completed");
    expect(conversation.latest_run?.error ?? "").toBe("");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    const assistantMessages = messageBody.items.filter((item) => item.role === "assistant");
    expect(assistantMessages).toHaveLength(1);
    expect(assistantMessages[0].content).toContain("ONE_LINE_SOURCE_REPAIRED");
    expect(assistantMessages[0].content).not.toContain("最终答案：已完成源码分析。");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
    await request.delete(`${backendBase}/api/workspaces/${encodeURIComponent(workspace.id)}`);
  }
});

test("real agent process keeps early and late diagnostics folded outside the answer", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-process-history-repo-")));
  fs.writeFileSync(
    path.join(repo, "README.md"),
    "Agent process history e2e workspace\nprocess_history_marker=ready\n",
    "utf8",
  );
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-process-history-")));
  const runtimeScript = path.join(runtimeDir, "process_history_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys, time",
      "sys.stdin.read()",
      "for index in range(1, 21):",
      "    print(f'thinking: PROCESS_STEP_{index:02d} reading workspace evidence', flush=True)",
      "    time.sleep(0.02)",
      "print('## 结论\\nAGENT_PROCESS_HISTORY_FINAL: 最终答案保持简洁，过程默认折叠。\\n\\n## 代码证据\\n- `README.md`: `process_history_marker` 表明 Agent 已读取工作区材料。\\n- `test/process-history`: 过程诊断由 thinking 通道输出，不进入最终答案。\\n\\n## 行为说明\\n1. Agent 连续输出 20 条过程诊断。\\n2. CodeTalk 只把最终答案展示在正文，把过程保留在默认折叠区。', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-process-history-e2e-${Date.now()}`;
  const runtimeName = `Process history runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} folded process`;

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
  const workspace = (await workspaceResp.json()) as { id: string };

  try {
    await page.goto("/ai", { waitUntil: "domcontentloaded" });
    const projectButton = page.locator("button").filter({ hasText: workspaceName }).first();
    await expect(projectButton).toBeVisible({ timeout: 20_000 });
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

    await page.getByLabel("AI 线程消息").fill("请执行 Agent 过程历史验证，只展示最终答案");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const assistantAnswer = page.locator(".ct-codex-message:not(.is-user)");
    await expect(assistantAnswer.filter({ hasText: "AGENT_PROCESS_HISTORY_FINAL" })).toBeVisible({ timeout: 30_000 });
    await expect(assistantAnswer.filter({ hasText: "PROCESS_STEP_01" })).toHaveCount(0);
    await expect(assistantAnswer.filter({ hasText: "PROCESS_STEP_20" })).toHaveCount(0);

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect(processDisclosure.getByText("最新：PROCESS_STEP_20 reading workspace evidence")).toBeVisible({
      timeout: 15_000,
    });
    await expect
      .poll(async () =>
        processDisclosure.locator("summary").evaluate((node) => {
          const element = node as HTMLElement;
          return element.scrollWidth <= element.clientWidth + 1;
        }),
      )
      .toBe(true);
    await expect(processDisclosure.getByText("PROCESS_STEP_01")).toBeHidden();
    await expect(processDisclosure.getByText("PROCESS_STEP_10 reading workspace evidence")).toBeHidden();
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("PROCESS_STEP_01 reading workspace evidence").first()).toBeVisible({
      timeout: 15_000,
    });
    await expect(processDisclosure.getByText("PROCESS_STEP_20 reading workspace evidence").last()).toBeVisible();

    await expect(page.getByText("生成诊断：默认折叠")).toHaveCount(0);

    let messageBody: { items: Array<{ role: string; content: string }> } = { items: [] };
    await expect
      .poll(async () => {
        const messagesResp = await request.get(
          `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
        );
        expect(messagesResp.ok()).toBeTruthy();
        messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
        return messageBody.items.some(
          (item) => item.role === "assistant" && item.content.includes("AGENT_PROCESS_HISTORY_FINAL"),
        );
      }, { timeout: 15_000 })
      .toBe(true);
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("AGENT_PROCESS_HISTORY_FINAL");
    expect(assistant?.content).not.toContain("PROCESS_STEP_01");
    expect(assistant?.content).not.toContain("PROCESS_STEP_20");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
    await request.delete(`${backendBase}/api/workspaces/${encodeURIComponent(workspace.id)}`);
  }
});

test("keeps resumed agent prompts focused on the current user turn", async ({ page, request }) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-resume-prompt-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Agent resume prompt e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-resume-prompt-")));
  const runtimeScript = path.join(runtimeDir, "resume_prompt_agent.py");
  const captureFile = path.join(runtimeDir, "captured-prompts.jsonl");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, pathlib, sys",
      "args = sys.argv[1:]",
      "resume = args[args.index('--resume') + 1] if '--resume' in args else ''",
      "prompt = sys.stdin.read()",
      `capture = pathlib.Path(${JSON.stringify(captureFile)})`,
      "capture.write_text((capture.read_text(encoding='utf-8') if capture.exists() else '') + json.dumps({'resume': resume, 'prompt': prompt}, ensure_ascii=False) + '\\n', encoding='utf-8')",
      "sid = 'ui-session-second' if resume else 'ui-session-first'",
      "marker = ('UI_RESUME_PROMPT_OK resumed:' + resume) if resume else 'UI_RESUME_PROMPT_OK fresh'",
      "print(json.dumps({'type':'system','subtype':'init','session_id': sid}, ensure_ascii=False), flush=True)",
      "print('## 结论\\n' + marker + '\\n\\n## 代码证据\\n- `README.md`: `Agent resume prompt e2e workspace` 表明 Agent 读取的是当前工作区。\\n- `CODETALK_AGENT_PROMPT_FILE`: CodeTalk 写入了完整本轮 prompt 供诊断核验。\\n\\n## 流程梳理\\n1. CodeTalk 拉起本地 Agent 子进程。\\n2. Agent 通过 session_id 建立或续接 CLI 会话。\\n3. 当前轮只接收当前用户任务，历史由 CLI session 承担。', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );

  const workspaceName = `ai-resume-prompt-e2e-${Date.now()}`;
  const runtimeName = `Resume prompt runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} resume prompt`;
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
      completion_mode: "process_exit",
      session_persistence: "resume_args",
      resume_args: [runtimeScript, "--resume", "{session_id}"],
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
    await expect(projectButton).toBeVisible({ timeout: 20_000 });
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

    const composer = page.getByLabel("AI 线程消息");
    await composer.fill("第一轮：读取工作区 README 并建立会话");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "UI_RESUME_PROMPT_OK fresh" })).toBeVisible({
      timeout: 30_000,
    });

    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);
    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "UI_RESUME_PROMPT_OK fresh" })).toBeVisible({
      timeout: 15_000,
    });

    await composer.fill("第二轮：只回答当前任务，不要重复历史");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(
      page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "UI_RESUME_PROMPT_OK resumed:ui-session-first" }),
    ).toBeVisible({ timeout: 30_000 });

    const captured = fs.readFileSync(captureFile, "utf8").trim().split("\n").map((line) => JSON.parse(line)) as Array<{
      resume: string;
      prompt: string;
    }>;
    expect(captured).toHaveLength(2);
    expect(captured[0].resume).toBe("");
    expect(captured[0].prompt.match(/第一轮/g)?.length).toBe(1);
    expect(captured[1].resume).toBe("ui-session-first");
    expect(captured[1].prompt).toContain("第二轮：只回答当前任务，不要重复历史");
    expect(captured[1].prompt.match(/第二轮/g)?.length).toBe(1);
    expect(captured[1].prompt).not.toContain("第一轮：读取工作区 README 并建立会话");
    expect(captured[1].prompt).not.toContain("UI_RESUME_PROMPT_OK fresh");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    expect(messageBody.items.filter((item) => item.role === "user")).toHaveLength(2);
    expect(messageBody.items.filter((item) => item.role === "assistant")).toHaveLength(2);
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
    await request.delete(`${backendBase}/api/workspaces/${encodeURIComponent(workspace.id)}`);
  }
});

test("renders native Codex task and tool events as Agent process diagnostics", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-codex-native-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Codex native event e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-codex-native-")));
  const runtimeScript = path.join(runtimeDir, "codex_native_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, sys, time",
      "sys.stdin.read()",
      "events = [",
      "  {'type':'thread.started','thread_id':'codex-native-e2e'},",
      "  {'type':'item.updated','item':{'type':'todo_list','todo_items':[{'id':'read','content':'读取 lib/nvmf 源码','status':'completed'},{'id':'sfmea','content':'生成 SFMEA','status':'in_progress'}]}},",
      "  {'type':'item.started','item':{'type':'mcp_tool_call','server':'gitnexus','tool':'search','arguments':{'query':'spdk_nvmf_connect'}}},",
      "  {'type':'item.completed','item':{'type':'command_execution','command':'rg spdk_nvmf_connect lib/nvmf','status':'completed','exit_code':0,'aggregated_output':'lib/nvmf/ctrlr.c: spdk_nvmf_connect'}},",
      "  {'type':'item.completed','item':{'type':'agent_message','text':'## 结论\\nCODEX_NATIVE_FINAL: 已完成源码分析并保留过程诊断。\\n\\n## 代码证据\\n- `lib/nvmf/ctrlr.c`: `nvmf_ctrlr_connect` 是 connect 入口候选。\\n- `test/nvmf`: 可承载连接路径回归。\\n\\n## 行为说明\\n1. Codex 原生任务和工具事件进入默认折叠的 Agent 过程。\\n2. 正文只展示最终分析结论。'}},",
      "]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "    time.sleep(0.05)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-codex-native-e2e-${Date.now()}`;
  const runtimeName = `Codex native runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} native events`;

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

    await page.getByLabel("AI 线程消息").fill("请用 Codex 原生事件读取源码并只展示最终答案");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const assistantAnswer = page.locator(".ct-codex-message:not(.is-user)");
    await expect(assistantAnswer.filter({ hasText: "CODEX_NATIVE_FINAL" })).toBeVisible({ timeout: 20_000 });
    await expect(assistantAnswer.filter({ hasText: "task_progress" })).toHaveCount(0);
    await expect(assistantAnswer.filter({ hasText: "spdk_nvmf_connect" })).toHaveCount(0);

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect(processDisclosure.getByText("task_progress")).toBeHidden();
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("task_progress read=completed").first()).toBeVisible({ timeout: 15_000 });
    await expect(processDisclosure.getByText("mcp:gitnexus/search").first()).toBeVisible();
    await expect(processDisclosure.getByText("rg spdk_nvmf_connect lib/nvmf").last()).toBeVisible();

    let messageBody: { items: Array<{ role: string; content: string }> } = { items: [] };
    await expect
      .poll(async () => {
        const messagesResp = await request.get(
          `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
        );
        expect(messagesResp.ok()).toBeTruthy();
        messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
        return messageBody.items.some(
          (item) => item.role === "assistant" && item.content.includes("CODEX_NATIVE_FINAL"),
        );
      }, { timeout: 15_000 })
      .toBe(true);
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("CODEX_NATIVE_FINAL");
    expect(assistant?.content).not.toContain("task_progress");
    expect(assistant?.content).not.toContain("spdk_nvmf_connect");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("renders Codex agent message deltas without dropping the final answer", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-codex-delta-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Codex delta event e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-codex-delta-")));
  const runtimeScript = path.join(runtimeDir, "codex_delta_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, sys, time",
      "sys.stdin.read()",
      "events = [",
      "  {'type':'thread.started','thread_id':'codex-delta-e2e'},",
      "  {'type':'item.completed','item':{'type':'command_execution','command':'rg nvmf_connect lib/nvmf','status':'completed','exit_code':0,'aggregated_output':'lib/nvmf/ctrlr.c: nvmf_connect'}},",
      "  {'type':'item.updated','item':{'type':'agent_message','delta':'CODEX_DELTA_FINAL: '}},",
      "  {'type':'item.updated','item':{'type':'agent_message','delta':'已基于源码完成增量回答。'}},",
      "  {'type':'item.completed','item':{'type':'agent_message'}},",
      "]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "    time.sleep(0.05)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-codex-delta-e2e-${Date.now()}`;
  const runtimeName = `Codex delta runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} delta events`;

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

    await page.getByLabel("AI 线程消息").fill("请用 Codex delta 事件读取源码并输出最终回答");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const assistantAnswer = page.locator(".ct-codex-message:not(.is-user)");
    await expect(assistantAnswer.filter({ hasText: "CODEX_DELTA_FINAL" })).toBeVisible({ timeout: 20_000 });
    await expect(assistantAnswer.filter({ hasText: "执行器没有返回有效内容" })).toHaveCount(0);
    await expect(assistantAnswer.filter({ hasText: "nvmf_connect" })).toHaveCount(0);

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("command: rg nvmf_connect lib/nvmf")).toBeVisible({ timeout: 15_000 });

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("CODEX_DELTA_FINAL: 已基于源码完成增量回答。");
    expect(assistant?.content).not.toContain("nvmf_connect");
    expect(assistant?.content).not.toContain("执行器没有返回有效内容");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("renders OpenAI response output item done as the final agent answer", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-response-done-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Responses API output item done e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-response-done-")));
  const runtimeScript = path.join(runtimeDir, "response_output_item_done_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, sys, time",
      "sys.stdin.read()",
      "answer = 'RESPONSE_DONE_FINAL: 已基于源码输出最终回答。\\n\\n## 代码证据\\n- `lib/iscsi/iscsi.c`: login 状态机。\\n\\n## 黑盒测试用例\\n1. 前置条件 target 已启动；步骤执行 iSCSI Login；预期结果 Login Response 可观测。'",
      "events = [",
      "  {'type':'response.created','response':{'id':'resp_response_done_e2e'}},",
      "  {'type':'response.output_item.added','item':{'id':'msg_1','type':'message','role':'assistant'}},",
      "  {'type':'item.completed','item':{'type':'command_execution','command':'rg iscsi_op_login lib/iscsi/iscsi.c','status':'completed','exit_code':0,'aggregated_output':'lib/iscsi/iscsi.c:iscsi_op_login_check_target'}},",
      "  {'type':'response.output_item.done','item':{'id':'msg_1','type':'message','role':'assistant','content':[{'type':'output_text','text':answer}]}},",
      "  {'type':'response.completed','response':{'status':'completed'}},",
      "]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "    time.sleep(0.05)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-response-done-e2e-${Date.now()}`;
  const runtimeName = `Responses output item runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} response done`;

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

    await page.getByLabel("AI 线程消息").fill("请用 Responses API output_item.done 事件读取源码并输出最终回答");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const assistantAnswer = page.locator(".ct-codex-message:not(.is-user)");
    await expect(assistantAnswer.filter({ hasText: "RESPONSE_DONE_FINAL" })).toBeVisible({ timeout: 20_000 });
    await expect(assistantAnswer.filter({ hasText: "执行器没有返回有效内容" })).toHaveCount(0);
    await expect(assistantAnswer.filter({ hasText: "response.output_item.done" })).toHaveCount(0);
    await expect(assistantAnswer.filter({ hasText: "iscsi_op_login_check_target" })).toHaveCount(0);

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("command: rg iscsi_op_login lib/iscsi/iscsi.c")).toBeVisible({
      timeout: 15_000,
    });

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("RESPONSE_DONE_FINAL");
    expect(assistant?.content).toContain("lib/iscsi/iscsi.c");
    expect(assistant?.content).not.toContain("response.output_item.done");
    expect(assistant?.content).not.toContain("iscsi_op_login_check_target");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("renders OpenAI response completed output as the final agent answer", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-response-completed-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Responses API completed output e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-response-completed-")));
  const runtimeScript = path.join(runtimeDir, "response_completed_output_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, sys, time",
      "sys.stdin.read()",
      "answer = 'RESPONSE_COMPLETED_FINAL: 已从 completed.output 提取最终回答。\\n\\n## 代码证据\\n- `lib/nvmf/ctrlr.c`: connect 路径。\\n\\n## 黑盒测试用例\\n1. 前置条件 NVMe-oF target 已启动；步骤发起 connect；预期结果连接状态可观测。'",
      "events = [",
      "  {'type':'response.created','response':{'id':'resp_completed_output_e2e'}},",
      "  {'type':'item.completed','item':{'type':'command_execution','command':'rg nvmf_connect lib/nvmf/ctrlr.c','status':'completed','exit_code':0,'aggregated_output':'lib/nvmf/ctrlr.c:nvmf_connect'}},",
      "  {'type':'response.completed','response':{'status':'completed','output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':answer}]}]}},",
      "]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "    time.sleep(0.05)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-response-completed-e2e-${Date.now()}`;
  const runtimeName = `Responses completed output runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} response completed`;

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

    await page.getByLabel("AI 线程消息").fill("请用 Responses API response.completed output 读取源码并输出最终回答");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const assistantAnswer = page.locator(".ct-codex-message:not(.is-user)");
    await expect(assistantAnswer.filter({ hasText: "下载完整产物" })).toBeVisible({ timeout: 20_000 });
    await expect(assistantAnswer.filter({ hasText: "执行器没有返回有效内容" })).toHaveCount(0);
    await expect(assistantAnswer.filter({ hasText: "response.completed" })).toHaveCount(0);
    await expect(assistantAnswer.filter({ hasText: "nvmf_connect" })).toHaveCount(0);
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "下载完整产物" }).hover();
    await page.getByRole("link", { name: "下载完整产物" }).click();
    const download = await downloadPromise;
    const artifactPath = testInfo.outputPath("response-completed-output-artifact.md");
    await download.saveAs(artifactPath);
    const artifact = fs.readFileSync(artifactPath, "utf8");
    expect(artifact).toContain("RESPONSE_COMPLETED_FINAL");
    expect(artifact).toContain("lib/nvmf/ctrlr.c");
    expect(artifact).not.toContain("response.completed");
    expect(artifact).not.toContain("command: rg nvmf_connect");

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("command: rg nvmf_connect lib/nvmf/ctrlr.c")).toBeVisible({
      timeout: 15_000,
    });

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("下载完整产物");
    expect(assistant?.content).not.toContain("response.completed");
    expect(assistant?.content).not.toContain("nvmf_connect");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("renders OpenAI response output text done as the final agent answer", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-output-text-done-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Responses API output_text.done e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-output-text-done-")));
  const runtimeScript = path.join(runtimeDir, "response_output_text_done_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, sys, time",
      "sys.stdin.read()",
      "partial = 'PARTIAL_SHOULD_BE_REPLACED: 临时增量。'",
      "answer = 'OUTPUT_TEXT_DONE_FINAL: 已从 output_text.done 提取最终回答。\\n\\n## 代码证据\\n- `lib/bdev/bdev.c`: submit 路径。\\n\\n## 流程梳理\\n1. 外部 Agent 先给增量片段。\\n2. done 事件给最终全文。'",
      "events = [",
      "  {'type':'response.created','response':{'id':'resp_output_text_done_e2e'}},",
      "  {'type':'response.output_text.delta','delta':partial},",
      "  {'type':'item.completed','item':{'type':'command_execution','command':'rg bdev_submit lib/bdev/bdev.c','status':'completed','exit_code':0,'aggregated_output':'lib/bdev/bdev.c:bdev_submit'}},",
      "  {'type':'response.output_text.done','text':answer},",
      "  {'type':'response.completed','response':{'status':'completed'}},",
      "]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "    time.sleep(0.05)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-output-text-done-e2e-${Date.now()}`;
  const runtimeName = `Responses output text done runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} output text done`;

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

    await page.getByLabel("AI 线程消息").fill("请用 Responses API output_text.done 读取源码并输出最终回答");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const assistantAnswer = page.locator(".ct-codex-message:not(.is-user)");
    await expect(assistantAnswer.filter({ hasText: "OUTPUT_TEXT_DONE_FINAL" })).toBeVisible({ timeout: 20_000 });
    await expect(assistantAnswer.filter({ hasText: "PARTIAL_SHOULD_BE_REPLACED" })).toHaveCount(0);
    await expect(assistantAnswer.filter({ hasText: "response.output_text.done" })).toHaveCount(0);
    await expect(assistantAnswer.filter({ hasText: "bdev_submit" })).toHaveCount(0);

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("command: rg bdev_submit lib/bdev/bdev.c")).toBeVisible({
      timeout: 15_000,
    });

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("OUTPUT_TEXT_DONE_FINAL");
    expect(assistant?.content).toContain("lib/bdev/bdev.c");
    expect(assistant?.content).not.toContain("PARTIAL_SHOULD_BE_REPLACED");
    expect(assistant?.content).not.toContain("response.output_text.done");
    expect(assistant?.content).not.toContain("bdev_submit");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("renders chat choice delta chunks without protocol JSON leakage", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-choice-delta-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Chat choice delta e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-choice-delta-")));
  const runtimeScript = path.join(runtimeDir, "chat_choice_delta_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, sys, time",
      "sys.stdin.read()",
      "events = [",
      "  {'choices':[{'delta':{'role':'assistant'}}]},",
      "  {'choices':[{'delta':{'content':'CHAT_CHOICE_FINAL: 已基于源码完成分析。\\n\\n## 代码证据\\n- `lib/bdev/bdev.c`: submit 路径。\\n- `test/bdev`: 可承载回归。\\n\\n'}}]},",
      "  {'type':'item.completed','item':{'type':'command_execution','command':'rg bdev_submit lib/bdev','status':'completed','exit_code':0,'aggregated_output':'lib/bdev/bdev.c:bdev_submit'}},",
      "  {'choices':[{'delta':{'content':'## 流程梳理\\n1. 读取 bdev submit 证据。\\n2. 输出外部可见结论。'}}]},",
      "  {'choices':[{'finish_reason':'stop'}]},",
      "]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "    time.sleep(0.05)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-choice-delta-e2e-${Date.now()}`;
  const runtimeName = `Chat choice delta runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} choice delta`;

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

    await page.getByLabel("AI 线程消息").fill("请用 Chat choices delta 读取源码并输出最终回答");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const assistantAnswer = page.locator(".ct-codex-message:not(.is-user)");
    await expect(assistantAnswer.filter({ hasText: "CHAT_CHOICE_FINAL" })).toBeVisible({ timeout: 20_000 });
    await expect(assistantAnswer.filter({ hasText: "finish_reason" })).toHaveCount(0);
    await expect(assistantAnswer.filter({ hasText: '"choices"' })).toHaveCount(0);
    await expect(assistantAnswer.filter({ hasText: "bdev_submit" })).toHaveCount(0);

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("command: rg bdev_submit lib/bdev")).toBeVisible({
      timeout: 15_000,
    });

    let messageBody: { items: Array<{ role: string; content: string }> } = { items: [] };
    await expect
      .poll(async () => {
        const messagesResp = await request.get(
          `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
        );
        expect(messagesResp.ok()).toBeTruthy();
        messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
        return messageBody.items.some(
          (item) => item.role === "assistant" && item.content.includes("CHAT_CHOICE_FINAL"),
        );
      }, { timeout: 15_000 })
      .toBe(true);
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("CHAT_CHOICE_FINAL");
    expect(assistant?.content).not.toContain("finish_reason");
    expect(assistant?.content).not.toContain('"choices"');
    expect(assistant?.content).not.toContain("bdev_submit");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("renders chat choice tool calls as folded Agent process diagnostics", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-choice-tools-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Chat choice tool calls e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-choice-tools-")));
  const runtimeScript = path.join(runtimeDir, "chat_choice_tool_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, sys, time",
      "sys.stdin.read()",
      "events = [",
      "  {'choices':[{'delta':{'tool_calls':[{'id':'call_1','type':'function','function':{'name':'search_source','arguments':'{\"query\":\"bdev submit\"}'}}]}}]},",
      "  {'choices':[{'delta':{'function_call':{'name':'read_file','arguments':'{\"path\":\"lib/bdev/bdev.c\"}'}}}]},",
      "  {'choices':[{'delta':{'content':'已读取工具过程并输出答案。\\n\\n## 代码证据\\n- `lib/bdev/bdev.c`: submit 路径。\\n- `test/bdev`: 可承载回归。\\n\\n## 流程梳理\\n1. Agent 先发出工具调用。\\n2. CodeTalk 把工具调用折叠到 Agent 过程。'}}]},",
      "]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "    time.sleep(0.05)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-choice-tools-e2e-${Date.now()}`;
  const runtimeName = `Chat choice tool runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} choice tools`;

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

    await page.getByLabel("AI 线程消息").fill("请用 Chat choices tool_calls 读取源码并输出最终回答");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const assistantAnswer = page.locator(".ct-codex-message:not(.is-user)");
    await expect(assistantAnswer.filter({ hasText: "已读取工具过程并输出答案" })).toBeVisible({ timeout: 20_000 });
    await expect(assistantAnswer.filter({ hasText: "tool_calls" })).toHaveCount(0);
    await expect(assistantAnswer.filter({ hasText: "search_source" })).toHaveCount(0);
    await expect(assistantAnswer.filter({ hasText: "read_file" })).toHaveCount(0);

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect(processDisclosure.getByText("search_source")).toBeHidden();
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.locator("p").filter({ hasText: /search_source.*bdev submit/ })).toBeVisible({
      timeout: 15_000,
    });
    await expect(processDisclosure.locator("p").filter({ hasText: /read_file.*lib\/bdev\/bdev\.c/ })).toBeVisible();

    let messageBody: { items: Array<{ role: string; content: string }> } = { items: [] };
    await expect
      .poll(async () => {
        const messagesResp = await request.get(
          `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
        );
        expect(messagesResp.ok()).toBeTruthy();
        messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
        return messageBody.items.some(
          (item) => item.role === "assistant" && item.content.includes("已读取工具过程并输出答案"),
        );
      }, { timeout: 15_000 })
      .toBe(true);
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("已读取工具过程并输出答案");
    expect(assistant?.content).not.toContain("tool_calls");
    expect(assistant?.content).not.toContain("search_source");
    expect(assistant?.content).not.toContain("read_file");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("keeps chat choice content visible when tool call shares the same delta", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-choice-combined-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Chat choice combined e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-choice-combined-")));
  const runtimeScript = path.join(runtimeDir, "chat_choice_combined_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, sys",
      "sys.stdin.read()",
      "event = {'choices':[{'delta':{",
      "  'tool_calls':[{'id':'call_1','type':'function','function':{'name':'search_source','arguments':'{\"query\":\"nvmf connect\"}'}}],",
      "  'content':'同包回答：已基于工具调用继续输出结论。\\n\\n## 代码证据\\n- `lib/nvmf`: connect 路径。\\n- `test/nvmf`: 可承载回归。\\n\\n## 流程梳理\\n1. 同一个 delta 先声明工具调用。\\n2. 同一个 delta 继续输出用户可见回答。'",
      "}}]}",
      "print(json.dumps(event, ensure_ascii=False), flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-choice-combined-e2e-${Date.now()}`;
  const runtimeName = `Chat choice combined runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} combined`;

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

    await page.getByLabel("AI 线程消息").fill("请处理同一个 Chat delta 内的 tool_calls 和 content");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const assistantAnswer = page.locator(".ct-codex-message:not(.is-user)");
    await expect(assistantAnswer.filter({ hasText: "同包回答：已基于工具调用继续输出结论" })).toBeVisible({
      timeout: 20_000,
    });
    await expect(assistantAnswer.filter({ hasText: "tool_calls" })).toHaveCount(0);
    await expect(assistantAnswer.filter({ hasText: "search_source" })).toHaveCount(0);

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    const processLine = processDisclosure.locator("p").filter({ hasText: /search_source.*nvmf connect/ });
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect(processLine).toBeHidden();
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processLine).toBeVisible({ timeout: 15_000 });

    let messageBody: { items: Array<{ role: string; content: string }> } = { items: [] };
    await expect
      .poll(async () => {
        const messagesResp = await request.get(
          `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
        );
        expect(messagesResp.ok()).toBeTruthy();
        messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
        return messageBody.items.some(
          (item) => item.role === "assistant" && item.content.includes("同包回答：已基于工具调用继续输出结论"),
        );
      }, { timeout: 15_000 })
      .toBe(true);
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("同包回答：已基于工具调用继续输出结论");
    expect(assistant?.content).not.toContain("tool_calls");
    expect(assistant?.content).not.toContain("search_source");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("folds streamed chat choice tool argument chunks into one Agent process line", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-choice-arg-stream-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Chat choice streamed tool args e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-choice-arg-stream-")));
  const runtimeScript = path.join(runtimeDir, "chat_choice_arg_stream_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, sys, time",
      "sys.stdin.read()",
      "events = [",
      "  {'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call_1','type':'function','function':{'name':'search_source','arguments':'{\"query\":\"'}}]}}]},",
      "  {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'nvmf connect'}}]}}]},",
      "  {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'\"}'}}]}}]},",
      "  {'choices':[{'delta':{'content':'工具参数完整后输出最终回答。\\n\\n## 代码证据\\n- `lib/nvmf`: connect 路径。\\n- `test/nvmf`: 可承载回归。\\n\\n## 流程梳理\\n1. 分段工具参数聚合成完整 JSON 后进入 Agent 过程。\\n2. 用户可见回答继续输出源码证据和结论。\\n\\n## 黑盒测试用例\\n- 前置条件：选择当前 workspace 和 Chat choices 执行器。\\n- 步骤：发送包含工具参数分段的任务。\\n- 预期结果：正文显示结论，Agent 过程只显示完整工具调用。'}}]},",
      "]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "    time.sleep(0.03)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-choice-arg-stream-e2e-${Date.now()}`;
  const runtimeName = `Chat choice arg stream runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} arg stream`;

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

    await page.getByLabel("AI 线程消息").fill("请处理分段流式 tool_calls arguments");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const assistantAnswer = page
      .locator(".ct-codex-message:not(.is-user)")
      .filter({ hasText: "工具参数完整后输出最终回答" })
      .first();
    await expect(assistantAnswer).toBeVisible({
      timeout: 20_000,
    });
    await expect(assistantAnswer).not.toContainText("tool_calls");
    await expect(assistantAnswer).not.toContainText("search_source");

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    const completeToolLine = processDisclosure.locator("p").filter({ hasText: /search_source.*nvmf connect/ });
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect(completeToolLine).toBeHidden();
    await processDisclosure.getByText("Agent 过程").click();
    await expect(completeToolLine).toBeVisible({ timeout: 15_000 });
    await expect(processDisclosure.locator("p").filter({ hasText: /function_call.*nvmf connect/ })).toHaveCount(0);
    await expect(processDisclosure.locator("p").filter({ hasText: /\{"query":"$/ })).toHaveCount(0);

    let messageBody: { items: Array<{ role: string; content: string }> } = { items: [] };
    await expect
      .poll(async () => {
        const messagesResp = await request.get(
          `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
        );
        expect(messagesResp.ok()).toBeTruthy();
        messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
        return messageBody.items.some(
          (item) => item.role === "assistant" && item.content.includes("工具参数完整后输出最终回答"),
        );
      }, { timeout: 15_000 })
      .toBe(true);
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("工具参数完整后输出最终回答");
    expect(assistant?.content).not.toContain("tool_calls");
    expect(assistant?.content).not.toContain("search_source");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("waits for streamed chat choice tool arguments when the name chunk is empty", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-choice-empty-args-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Chat choice empty argument e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-choice-empty-args-")));
  const runtimeScript = path.join(runtimeDir, "chat_choice_empty_args_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, sys, time",
      "sys.stdin.read()",
      "events = [",
      "  {'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call_1','type':'function','function':{'name':'search_source','arguments':''}}]}}]},",
      "  {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'{\"query\":\"'}}]}}]},",
      "  {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'nvmf connect'}}]}}]},",
      "  {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'\"}'}}]}}]},",
      "  {'choices':[{'delta':{'content':'空参数首段聚合后输出最终回答。\\n\\n## 代码证据\\n- `lib/nvmf`: connect 路径。\\n- `test/nvmf`: 可承载回归。\\n\\n## 流程梳理\\n1. 第一段只有工具名和空参数，不单独刷进过程。\\n2. 参数补齐后，Agent 过程只显示完整工具调用。\\n\\n## 黑盒测试用例\\n- 前置条件：选择当前 workspace 和 Chat choices 执行器。\\n- 步骤：发送空参数首段加后续参数分片。\\n- 预期结果：正文显示结论，过程不出现孤立工具名。'}}]},",
      "]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "    time.sleep(0.03)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-choice-empty-args-e2e-${Date.now()}`;
  const runtimeName = `Chat choice empty args runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} empty args`;

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

    await page.getByLabel("AI 线程消息").fill("请处理工具名首段空参数的流式调用");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const assistantAnswer = page
      .locator(".ct-codex-message:not(.is-user)")
      .filter({ hasText: "空参数首段聚合后输出最终回答" })
      .first();
    await expect(assistantAnswer).toBeVisible({
      timeout: 20_000,
    });
    await expect(assistantAnswer).not.toContainText("search_source");

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    const completeToolLine = processDisclosure.locator("p").filter({ hasText: /search_source.*nvmf connect/ });
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect(completeToolLine).toBeHidden();
    await processDisclosure.getByText("Agent 过程").click();
    await expect(completeToolLine).toBeVisible({ timeout: 15_000 });
    await expect(processDisclosure.locator("p").filter({ hasText: /^search_source$/ })).toHaveCount(0);
    await expect(processDisclosure.locator("p").filter({ hasText: /function_call.*nvmf connect/ })).toHaveCount(0);

    let messageBody: { items: Array<{ role: string; content: string }> } = { items: [] };
    await expect
      .poll(async () => {
        const messagesResp = await request.get(
          `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
        );
        expect(messagesResp.ok()).toBeTruthy();
        messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
        return messageBody.items.some(
          (item) => item.role === "assistant" && item.content.includes("空参数首段聚合后输出最终回答"),
        );
      }, { timeout: 15_000 })
      .toBe(true);
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("空参数首段聚合后输出最终回答");
    expect(assistant?.content).not.toContain("search_source");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("waits for streamed chat choice tool arguments when the first chunk only has the tool name", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-choice-name-only-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Chat choice name-only argument e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-choice-name-only-")));
  const runtimeScript = path.join(runtimeDir, "chat_choice_name_only_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, sys, time",
      "sys.stdin.read()",
      "events = [",
      "  {'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call_1','type':'function','function':{'name':'search_source'}}]}}]},",
      "  {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'{\"query\":\"'}}]}}]},",
      "  {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'nvmf connect'}}]}}]},",
      "  {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'\"}'}}]}}]},",
      "  {'choices':[{'delta':{'content':'工具名首段聚合后输出最终回答。\\n\\n## 代码证据\\n- `lib/nvmf`: connect 路径。\\n- `test/nvmf`: 可承载回归。\\n\\n## 流程梳理\\n1. 第一段只有工具名，不单独刷进过程。\\n2. 参数补齐后，Agent 过程只显示完整工具调用。\\n\\n## 黑盒测试用例\\n- 前置条件：选择当前 workspace 和 Chat choices 执行器。\\n- 步骤：发送工具名首段加后续参数分片。\\n- 预期结果：正文显示结论，过程不出现孤立工具名或协议诊断行。'}}]},",
      "]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "    time.sleep(0.03)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-choice-name-only-e2e-${Date.now()}`;
  const runtimeName = `Chat choice name-only runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} name only`;

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

    await page.getByLabel("AI 线程消息").fill("请处理工具名首段无参数字段的流式调用");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const assistantAnswer = page
      .locator(".ct-codex-message:not(.is-user)")
      .filter({ hasText: "工具名首段聚合后输出最终回答" })
      .first();
    await expect(assistantAnswer).toBeVisible({
      timeout: 20_000,
    });
    await expect(assistantAnswer).not.toContainText("search_source");
    await expect(assistantAnswer).not.toContainText("function_call");

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    const completeToolLine = processDisclosure.locator("p").filter({ hasText: /search_source.*nvmf connect/ });
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect(completeToolLine).toBeHidden();
    await processDisclosure.getByText("Agent 过程").click();
    await expect(completeToolLine).toBeVisible({ timeout: 15_000 });
    await expect(processDisclosure.locator("p").filter({ hasText: /^search_source$/ })).toHaveCount(0);
    await expect(processDisclosure.locator("p").filter({ hasText: /function_call.*nvmf connect/ })).toHaveCount(0);

    let messageBody: { items: Array<{ role: string; content: string }> } = { items: [] };
    await expect
      .poll(async () => {
        const messagesResp = await request.get(
          `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
        );
        expect(messagesResp.ok()).toBeTruthy();
        messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
        return messageBody.items.some(
          (item) => item.role === "assistant" && item.content.includes("工具名首段聚合后输出最终回答"),
        );
      }, { timeout: 15_000 })
      .toBe(true);
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("工具名首段聚合后输出最终回答");
    expect(assistant?.content).not.toContain("search_source");
    expect(assistant?.content).not.toContain("function_call");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("keeps interleaved chat choice tool argument streams separated in Agent process", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-choice-interleaved-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Chat choice interleaved tool args e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-choice-interleaved-")));
  const runtimeScript = path.join(runtimeDir, "chat_choice_interleaved_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, sys, time",
      "sys.stdin.read()",
      "events = [",
      "  {'choices':[{'delta':{'tool_calls':[",
      "    {'index':0,'id':'call_search','type':'function','function':{'name':'search_source','arguments':'{\"query\":\"'}},",
      "    {'index':1,'id':'call_read','type':'function','function':{'name':'read_file','arguments':'{\"path\":\"'}}",
      "  ]}}]},",
      "  {'choices':[{'delta':{'tool_calls':[{'index':1,'function':{'arguments':'lib/nvmf/ctrlr.c'}}]}}]},",
      "  {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'nvmf connect'}}]}}]},",
      "  {'choices':[{'delta':{'tool_calls':[{'index':1,'function':{'arguments':'\"}'}}]}}]},",
      "  {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'\"}'}}]}}]},",
      "  {'choices':[{'delta':{'content':'交错工具参数聚合后输出最终回答。\\n\\n## 代码证据\\n- `lib/nvmf/ctrlr.c`: connect 控制器路径。\\n- `test/nvmf`: 可承载回归。\\n\\n## 流程梳理\\n1. 两个工具调用的参数分片交错到达。\\n2. Agent 过程分别显示完整 search 和 read 调用。\\n\\n## 黑盒测试用例\\n- 前置条件：选择当前 workspace 和 Chat choices 执行器。\\n- 步骤：发送双工具交错参数分片。\\n- 预期结果：正文干净，过程不串线。'}}]},",
      "]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "    time.sleep(0.03)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-choice-interleaved-e2e-${Date.now()}`;
  const runtimeName = `Chat choice interleaved runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} interleaved`;

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

    await page.getByLabel("AI 线程消息").fill("请处理两个工具调用交错参数分片");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const assistantAnswer = page
      .locator(".ct-codex-message:not(.is-user)")
      .filter({ hasText: "交错工具参数聚合后输出最终回答" })
      .first();
    await expect(assistantAnswer).toBeVisible({
      timeout: 20_000,
    });
    await expect(assistantAnswer).not.toContainText("search_source");
    await expect(assistantAnswer).not.toContainText("read_file");

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    const searchLine = processDisclosure.locator("p").filter({ hasText: /search_source.*nvmf connect/ });
    const readLine = processDisclosure.locator("p").filter({ hasText: /read_file.*lib\/nvmf\/ctrlr\.c/ });
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect(searchLine).toBeHidden();
    await expect(readLine).toBeHidden();
    await processDisclosure.getByText("Agent 过程").click();
    await expect(searchLine).toBeVisible({ timeout: 15_000 });
    await expect(readLine).toBeVisible({ timeout: 15_000 });
    await expect(processDisclosure.locator("p").filter({ hasText: /search_source.*lib\/nvmf\/ctrlr\.c/ })).toHaveCount(0);
    await expect(processDisclosure.locator("p").filter({ hasText: /read_file.*nvmf connect/ })).toHaveCount(0);
    await expect(processDisclosure.locator("p").filter({ hasText: /function_call/ })).toHaveCount(0);

    let messageBody: { items: Array<{ role: string; content: string }> } = { items: [] };
    await expect
      .poll(async () => {
        const messagesResp = await request.get(
          `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
        );
        expect(messagesResp.ok()).toBeTruthy();
        messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
        return messageBody.items.some(
          (item) => item.role === "assistant" && item.content.includes("交错工具参数聚合后输出最终回答"),
        );
      }, { timeout: 15_000 })
      .toBe(true);
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("交错工具参数聚合后输出最终回答");
    expect(assistant?.content).not.toContain("search_source");
    expect(assistant?.content).not.toContain("read_file");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("downloads a Markdown artifact written by the agent runtime", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-agent-artifact-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Agent artifact e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-artifact-")));
  const runtimeScript = path.join(runtimeDir, "artifact_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import os, pathlib, sys, time",
      "sys.stdin.read()",
      "artifact_dir = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
      "artifact_dir.mkdir(parents=True, exist_ok=True)",
      "report = '# Agent 生成报告\\n\\n## 黑盒测试用例\\n' + ''.join([f'{index}. TC-{index:02d}：前置条件 target 已启动。步骤执行 SPDK 登录场景。预期结果可观测。\\n' for index in range(1, 9)])",
      "(artifact_dir / 'spdk-blackbox.md').write_text(report, encoding='utf-8')",
      "print('已生成文件：spdk-blackbox.md', flush=True)",
      "time.sleep(0.05)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-agent-artifact-e2e-${Date.now()}`;
  const runtimeName = `Agent artifact runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} artifact file`;

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

    await page.getByLabel("AI 线程消息").fill("生成完整黑盒测试用例并保存为文件");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const assistantAnswer = page.locator(".ct-codex-message:not(.is-user)");
    await expect(assistantAnswer.filter({ hasText: "已生成结构化产物" })).toBeVisible({ timeout: 20_000 });
    await expect(assistantAnswer.filter({ hasText: "TC-08" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "下载完整产物" })).toBeVisible({ timeout: 15_000 });

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "下载完整产物" }).hover();
    await page.getByRole("link", { name: "下载完整产物" }).click();
    const download = await downloadPromise;
    const artifactPath = testInfo.outputPath("agent-written-artifact.md");
    await download.saveAs(artifactPath);
    const artifact = fs.readFileSync(artifactPath, "utf8");
    expect(artifact).toContain("## 黑盒测试用例");
    expect(artifact).toContain("TC-08");
    expect(artifact).not.toContain("已生成文件：spdk-blackbox.md");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("已生成结构化产物");
    expect(assistant?.content).not.toContain("TC-08");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("downloads complete inline SFMEA and black-box output from the agent runtime", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-inline-complete-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Inline complete artifact e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-inline-complete-")));
  const runtimeScript = path.join(runtimeDir, "inline_complete_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "sys.stdin.read()",
      "print('## 结论\\n已完成 SPDK connect 完整测试设计。\\n\\n## 代码证据\\n- `lib/nvmf/ctrlr.c`: `nvmf_ctrlr_connect`。\\n- `test/nvmf`: 可承载连接测试。\\n\\n## 流程梳理\\n1. initiator 发起连接。\\n2. target 建立 controller。\\n\\n## SFMEA\\n| failure mode | cause | effect | severity | occurrence | detection | RPN | mitigation |\\n| connect timeout | 网络抖动 | 连接失败 | 8 | 3 | 4 | 96 | 增加超时与重试观测 |\\n\\n## 黑盒测试用例\\n1. 用例：正常连接；前置条件：target 已启动；步骤：发起连接；预期结果：连接成功；观测点：日志和状态。\\n2. 用例：连接超时；前置条件：注入网络延迟；步骤：发起连接；预期结果：超时失败且可重试；观测点：错误码和日志。', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-inline-complete-e2e-${Date.now()}`;
  const runtimeName = `Inline complete runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} inline complete`;

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

    await page.getByLabel("AI 线程消息").fill("请输出完整的代码分析、流程梳理、SFMEA 和黑盒测试用例");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const assistantAnswer = page.locator(".ct-codex-message:not(.is-user)");
    await expect(assistantAnswer.filter({ hasText: "已生成结构化产物" })).toBeVisible({ timeout: 30_000 });
    await expect(assistantAnswer.filter({ hasText: "connect timeout" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "下载完整产物" })).toBeVisible({ timeout: 15_000 });

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "下载完整产物" }).hover();
    await page.getByRole("link", { name: "下载完整产物" }).click();
    const download = await downloadPromise;
    const artifactPath = testInfo.outputPath("inline-complete-artifact.md");
    await download.saveAs(artifactPath);
    const artifact = fs.readFileSync(artifactPath, "utf8");
    expect(artifact).toContain("## SFMEA");
    expect(artifact).toContain("connect timeout");
    expect(artifact).toContain("## 黑盒测试用例");
    expect(artifact).toContain("用例：连接超时");

    const exportPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出" }).hover();
    await page.getByRole("button", { name: "导出" }).click();
    const exportDownload = await exportPromise;
    const exportPath = testInfo.outputPath("inline-complete-thread-export.md");
    await exportDownload.saveAs(exportPath);
    const exported = fs.readFileSync(exportPath, "utf8");
    expect(exported).toContain("### 附件与产物");
    expect(exported).toContain("下载完整产物");
    expect(exported).toContain(`/api/ai/conversations/${threadId}/runs/`);
    expect(exported).toContain("完整测试设计/SFMEA/黑盒用例已保存为下载产物");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("keeps live streaming SFMEA and black-box output compact while preserving the downloaded artifact", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-live-artifact-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Live compact artifact e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-live-artifact-")));
  const runtimeScript = path.join(runtimeDir, "live_artifact_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys, time",
      "sys.stdin.read()",
      "def emit(line=''):",
      "    print(line, flush=True)",
      "    time.sleep(0.04)",
      "emit('## 结论')",
      "emit('LIVE_ARTIFACT_FINAL: 已完成 SPDK connect 长结构化测试设计。')",
      "emit('')",
      "emit('## 代码证据')",
      "emit('- `lib/nvmf/ctrlr.c`: `nvmf_ctrlr_connect`。')",
      "emit('- `test/nvmf`: 可承载 connect/reconnect 黑盒回归。')",
      "emit('')",
      "emit('## 流程梳理')",
      "for index in range(1, 10):",
      "    emit(f'{index}. LIVE_FLOW_STEP_{index:02d}: initiator 与 target 完成 connect 阶段 {index}。')",
      "emit('')",
      "emit('## SFMEA')",
      "emit('| failure mode | cause | effect | severity | occurrence | detection | RPN | mitigation |')",
      "emit('| --- | --- | --- | --- | --- | --- | --- | --- |')",
      "for index in range(1, 26):",
      "    emit(f'| LIVE_SFMEA_ROW_{index:02d} | transport edge case {index} | IO pause {index} | 8 | 3 | 4 | {90 + index} | 增加外部日志、RPC 状态和重连观测 |')",
      "emit('')",
      "emit('## 黑盒测试用例')",
      "for index in range(1, 26):",
      "    emit(f'{index}. LIVE_BLACKBOX_TC_{index:02d}: 前置条件 target 已启动；步骤执行 connect/reconnect 场景 {index}；预期结果返回明确状态且不中断后续 IO；观测点为 RPC、日志、连接状态和延迟指标。')",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-live-artifact-e2e-${Date.now()}`;
  const runtimeName = `Live artifact runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} live compact`;

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

    await page
      .getByLabel("AI 线程消息")
      .fill("请输出完整的代码分析、流程梳理、SFMEA 和黑盒测试用例，并在生成中保持页面可读");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const reader = page.getByLabel("AI 线程对话内容");
    await expect(page.getByText("正在生成结构化产物，完成后会提供下载文件。")).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByRole("button", { name: "停止" })).toBeVisible({ timeout: 10_000 });
    await expect(reader).not.toContainText("LIVE_SFMEA_ROW_25");
    await expect(reader).not.toContainText("LIVE_BLACKBOX_TC_25");

    const assistantAnswer = page.locator(".ct-codex-message:not(.is-user)");
    await expect(assistantAnswer.filter({ hasText: "已生成结构化产物" })).toBeVisible({ timeout: 35_000 });
    await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0, { timeout: 15_000 });
    await expect(page.getByRole("link", { name: "下载完整产物" })).toBeVisible({ timeout: 15_000 });
    await expect(reader).not.toContainText("LIVE_SFMEA_ROW_25");
    await expect(reader).not.toContainText("LIVE_BLACKBOX_TC_25");

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "下载完整产物" }).hover();
    await page.getByRole("link", { name: "下载完整产物" }).click();
    const download = await downloadPromise;
    const artifactPath = testInfo.outputPath("live-artifact.md");
    await download.saveAs(artifactPath);
    const artifact = fs.readFileSync(artifactPath, "utf8");
    expect(artifact).toContain("LIVE_ARTIFACT_FINAL");
    expect(artifact).toContain("LIVE_SFMEA_ROW_25");
    expect(artifact).toContain("LIVE_BLACKBOX_TC_25");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    const assistant = [...messageBody.items].reverse().find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("下载完整产物");
    expect(assistant?.content).not.toContain("LIVE_SFMEA_ROW_25");
    expect(assistant?.content).not.toContain("LIVE_BLACKBOX_TC_25");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("downloads four-piece inline SFMEA and black-box output without requiring complete wording", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-inline-four-piece-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Inline four-piece artifact e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-inline-four-piece-")));
  const runtimeScript = path.join(runtimeDir, "inline_four_piece_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "sys.stdin.read()",
      "print('## 结论\\n已完成 SPDK connect 测试设计。\\n\\n## 代码证据\\n- `lib/nvmf/ctrlr.c`: `nvmf_ctrlr_connect`。\\n- `test/nvmf`: 可承载连接测试。\\n\\n## 流程梳理\\n1. initiator 发起连接。\\n2. target 建立 controller。\\n\\n## SFMEA\\n| failure mode | cause | effect | severity | occurrence | detection | RPN | mitigation |\\n| connect timeout | 网络抖动 | 连接失败 | 8 | 3 | 4 | 96 | 增加超时与重试观测 |\\n\\n## 黑盒测试用例\\n1. 用例：正常连接；前置条件：target 已启动；步骤：发起连接；预期结果：连接成功；观测点：日志和状态。\\n2. 用例：连接超时；前置条件：注入网络延迟；步骤：发起连接；预期结果：超时失败且可重试；观测点：错误码和日志。', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-inline-four-piece-e2e-${Date.now()}`;
  const runtimeName = `Inline four-piece runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} inline four-piece`;

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
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);

    await page.getByLabel("AI 线程消息").fill("请输出代码分析、流程梳理、SFMEA 和黑盒测试用例");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const assistantAnswer = page.locator(".ct-codex-message:not(.is-user)");
    await expect(assistantAnswer.filter({ hasText: "已生成结构化产物" })).toBeVisible({ timeout: 30_000 });
    await expect(assistantAnswer.filter({ hasText: "connect timeout" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "下载完整产物" })).toBeVisible({ timeout: 15_000 });

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "下载完整产物" }).hover();
    await page.getByRole("link", { name: "下载完整产物" }).click();
    const download = await downloadPromise;
    const artifactPath = testInfo.outputPath("inline-four-piece-artifact.md");
    await download.saveAs(artifactPath);
    const artifact = fs.readFileSync(artifactPath, "utf8");
    expect(artifact).toContain("## 代码证据");
    expect(artifact).toContain("## 流程梳理");
    expect(artifact).toContain("## SFMEA");
    expect(artifact).toContain("connect timeout");
    expect(artifact).toContain("## 黑盒测试用例");
    expect(artifact).toContain("用例：连接超时");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("downloads a concise Markdown artifact written by the agent runtime", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-short-artifact-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Short agent artifact e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-short-artifact-")));
  const runtimeScript = path.join(runtimeDir, "short_artifact_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import os, pathlib, sys",
      "sys.stdin.read()",
      "artifact_dir = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
      "artifact_dir.mkdir(parents=True, exist_ok=True)",
      "(artifact_dir / 'handoff.md').write_text('# Agent Handoff\\n\\nConcise saved file.\\n', encoding='utf-8')",
      "print('已生成文件：handoff.md', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-short-artifact-e2e-${Date.now()}`;
  const runtimeName = `Short artifact runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} concise artifact`;

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

    await page.getByLabel("AI 线程消息").fill("保存一个简短交接文件");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const assistantAnswer = page.locator(".ct-codex-message:not(.is-user)");
    await expect(assistantAnswer.filter({ hasText: "已生成结构化产物" })).toBeVisible({ timeout: 20_000 });
    await expect(assistantAnswer.filter({ hasText: "Concise saved file" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "下载完整产物" })).toBeVisible({ timeout: 15_000 });

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "下载完整产物" }).hover();
    await page.getByRole("link", { name: "下载完整产物" }).click();
    const download = await downloadPromise;
    const artifactPath = testInfo.outputPath("short-agent-artifact.md");
    await download.saveAs(artifactPath);
    const artifact = fs.readFileSync(artifactPath, "utf8");
    expect(artifact).toContain("# Agent Handoff");
    expect(artifact).toContain("Concise saved file");
    expect(artifact).not.toContain("已生成文件：handoff.md");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("已生成结构化产物");
    expect(assistant?.content).not.toContain("Concise saved file");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("downloads all Markdown artifacts written by the agent runtime", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-multi-artifact-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Multi agent artifact e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-multi-artifact-")));
  const runtimeScript = path.join(runtimeDir, "multi_artifact_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import os, pathlib, sys",
      "sys.stdin.read()",
      "artifact_dir = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
      "artifact_dir.mkdir(parents=True, exist_ok=True)",
      "(artifact_dir / 'flow.md').write_text('# 流程梳理\\n\\nFLOW_ARTIFACT_ONLY\\n', encoding='utf-8')",
      "(artifact_dir / 'sfmea.md').write_text('# SFMEA\\n\\nSFMEA_ARTIFACT_ONLY\\n', encoding='utf-8')",
      "print('已生成文件：flow.md sfmea.md', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-multi-artifact-e2e-${Date.now()}`;
  const runtimeName = `Multi artifact runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} multi artifact`;

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

    await page.getByLabel("AI 线程消息").fill("保存流程梳理和 SFMEA 两个文件");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const assistantAnswer = page.locator(".ct-codex-message:not(.is-user)");
    await expect(assistantAnswer.filter({ hasText: "已生成结构化产物" })).toBeVisible({ timeout: 20_000 });
    await expect(assistantAnswer.filter({ hasText: "FLOW_ARTIFACT_ONLY" })).toHaveCount(0);
    await expect(assistantAnswer.filter({ hasText: "SFMEA_ARTIFACT_ONLY" })).toHaveCount(0);

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "下载完整产物" }).hover();
    await page.getByRole("link", { name: "下载完整产物" }).click();
    const download = await downloadPromise;
    const artifactPath = testInfo.outputPath("multi-agent-artifacts.md");
    await download.saveAs(artifactPath);
    const artifact = fs.readFileSync(artifactPath, "utf8");
    expect(artifact).toContain("flow.md");
    expect(artifact).toContain("sfmea.md");
    expect(artifact).toContain("FLOW_ARTIFACT_ONLY");
    expect(artifact).toContain("SFMEA_ARTIFACT_ONLY");
    expect(artifact).not.toContain("已生成文件：flow.md sfmea.md");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("已生成结构化产物");
    expect(assistant?.content).not.toContain("FLOW_ARTIFACT_ONLY");
    expect(assistant?.content).not.toContain("SFMEA_ARTIFACT_ONLY");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("redacts secrets from a Markdown artifact written by the agent runtime", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-redacted-artifact-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Redacted agent artifact e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-redacted-artifact-")));
  const runtimeScript = path.join(runtimeDir, "redacted_artifact_agent.py");
  const leakedKey = "sk-agentArtifactE2ESecret1234567890";
  const leakedToken = "artifactE2ETokenLeak12345";
  fs.writeFileSync(
    runtimeScript,
    [
      "import os, pathlib, sys",
      "sys.stdin.read()",
      "artifact_dir = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
      "artifact_dir.mkdir(parents=True, exist_ok=True)",
      `body = '# Agent Report\\n\\nSAFE_ARTIFACT_BODY\\n\\napi_key=${leakedKey}\\ntoken=${leakedToken}\\n'`,
      "(artifact_dir / 'leaky.md').write_text(body, encoding='utf-8')",
      "print('已生成文件：leaky.md', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-redacted-artifact-e2e-${Date.now()}`;
  const runtimeName = `Redacted artifact runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} redacted artifact`;

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

    await page.getByLabel("AI 线程消息").fill("保存一个报告文件，文件里不能泄露密钥");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const assistantAnswer = page.locator(".ct-codex-message:not(.is-user)");
    await expect(assistantAnswer.filter({ hasText: "已生成结构化产物" })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("link", { name: "下载完整产物" })).toBeVisible({ timeout: 15_000 });

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "下载完整产物" }).hover();
    await page.getByRole("link", { name: "下载完整产物" }).click();
    const download = await downloadPromise;
    const artifactPath = testInfo.outputPath("redacted-agent-artifact.md");
    await download.saveAs(artifactPath);
    const artifact = fs.readFileSync(artifactPath, "utf8");
    expect(artifact).toContain("SAFE_ARTIFACT_BODY");
    expect(artifact).toContain("<redacted>");
    expect(artifact).not.toContain(leakedKey);
    expect(artifact).not.toContain(leakedToken);

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("已生成结构化产物");
    expect(assistant?.content).not.toContain(leakedKey);
    expect(assistant?.content).not.toContain(leakedToken);
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("downloads a JSON artifact written by the agent runtime without Markdown-only copy", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-json-artifact-")));
  fs.writeFileSync(path.join(repo, "README.md"), "JSON agent artifact e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-json-artifact-")));
  const runtimeScript = path.join(runtimeDir, "json_artifact_agent.py");
  const leakedKey = "sk-jsonArtifactE2ESecret1234567890";
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, os, pathlib, sys",
      "sys.stdin.read()",
      "artifact_dir = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
      "artifact_dir.mkdir(parents=True, exist_ok=True)",
      "payload = {",
      "  'sfmea': [{'failure_mode': 'connect timeout', 'rpn': 216}],",
      "  'black_box_cases': [{'id': 'TC-NVMF-JSON-01', 'expected': 'observable timeout'}],",
      `  'api_key': '${leakedKey}',`,
      "}",
      "(artifact_dir / 'sfmea_cases.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')",
      "print('已生成文件：sfmea_cases.json', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-json-artifact-e2e-${Date.now()}`;
  const runtimeName = `JSON artifact runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} json artifact`;

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

    await page.getByLabel("AI 线程消息").fill("保存 SFMEA 和黑盒测试用例 JSON 文件");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const assistantAnswer = page.locator(".ct-codex-message:not(.is-user)");
    await expect(assistantAnswer.filter({ hasText: "已生成结构化产物" })).toBeVisible({ timeout: 20_000 });
    await expect(assistantAnswer.filter({ hasText: "完整 Markdown" })).toHaveCount(0);
    await expect(assistantAnswer.filter({ hasText: "TC-NVMF-JSON-01" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "下载完整产物" })).toBeVisible({ timeout: 15_000 });

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "下载完整产物" }).hover();
    await page.getByRole("link", { name: "下载完整产物" }).click();
    const download = await downloadPromise;
    const artifactPath = testInfo.outputPath("json-agent-artifact.md");
    await download.saveAs(artifactPath);
    const artifact = fs.readFileSync(artifactPath, "utf8");
    expect(artifact).toContain('"sfmea": [');
    expect(artifact).toContain('"black_box_cases": [');
    expect(artifact).toContain("TC-NVMF-JSON-01");
    expect(artifact).toContain("<redacted>");
    expect(artifact).not.toContain(leakedKey);

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("已生成结构化产物");
    expect(assistant?.content).not.toContain("完整 Markdown");
    expect(assistant?.content).not.toContain("TC-NVMF-JSON-01");
    expect(assistant?.content).not.toContain(leakedKey);
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("keeps agent audit artifacts out of the user download package", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-audit-artifact-")));
  fs.writeFileSync(path.join(repo, "README.md"), "Audit artifact filtering e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-audit-artifact-")));
  const runtimeScript = path.join(runtimeDir, "audit_artifact_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import os, pathlib, sys",
      "sys.stdin.read()",
      "artifact_dir = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
      "artifact_dir.mkdir(parents=True, exist_ok=True)",
      "(artifact_dir / 'report.md').write_text('# 用户结果\\n\\nVISIBLE_REPORT_RESULT\\n', encoding='utf-8')",
      "(artifact_dir / 'raw_output.jsonl').write_text('{\"event\":\"RAW_AGENT_TRACE_SHOULD_NOT_DOWNLOAD\"}\\n', encoding='utf-8')",
      "(artifact_dir / 'diagnostics.txt').write_text('DIAGNOSTIC_TRACE_SHOULD_NOT_DOWNLOAD\\n', encoding='utf-8')",
      "print('已生成文件：report.md raw_output.jsonl diagnostics.txt', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-audit-artifact-e2e-${Date.now()}`;
  const runtimeName = `Audit artifact runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} audit artifact`;

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

    await page.getByLabel("AI 线程消息").fill("保存最终报告，同时保留内部执行日志");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const assistantAnswer = page.locator(".ct-codex-message:not(.is-user)");
    await expect(assistantAnswer.filter({ hasText: "已生成结构化产物" })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("link", { name: "下载完整产物" })).toBeVisible({ timeout: 15_000 });

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "下载完整产物" }).hover();
    await page.getByRole("link", { name: "下载完整产物" }).click();
    const download = await downloadPromise;
    const artifactPath = testInfo.outputPath("filtered-agent-artifacts.md");
    await download.saveAs(artifactPath);
    const artifact = fs.readFileSync(artifactPath, "utf8");
    expect(artifact).toContain("VISIBLE_REPORT_RESULT");
    expect(artifact).not.toContain("RAW_AGENT_TRACE_SHOULD_NOT_DOWNLOAD");
    expect(artifact).not.toContain("DIAGNOSTIC_TRACE_SHOULD_NOT_DOWNLOAD");
    expect(artifact).not.toContain("raw_output.jsonl");
    expect(artifact).not.toContain("diagnostics.txt");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("已生成结构化产物");
    expect(assistant?.content).not.toContain("RAW_AGENT_TRACE_SHOULD_NOT_DOWNLOAD");
    expect(assistant?.content).not.toContain("DIAGNOSTIC_TRACE_SHOULD_NOT_DOWNLOAD");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
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
        expect.objectContaining({ role: "assistant", content: expect.stringContaining("fresh claude print") }),
        expect.objectContaining({ role: "assistant", content: expect.stringContaining("resumed claude:claude-e2e-first") }),
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
        expect.objectContaining({ role: "assistant", content: expect.stringContaining("fresh opencode run") }),
        expect.objectContaining({ role: "assistant", content: expect.stringContaining("resumed opencode:opencode-e2e-first") }),
      ]),
    );
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
    await request.delete(`${backendBase}/api/workspaces/${encodeURIComponent(workspace.id)}`);
  }
});

test("self-heals a stale OpenCode resume session through the real AI thread UI", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-stale-opencode-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "OpenCode stale resume e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-opencode-stale-")));
  const runtimeScript = path.join(runtimeDir, "fake_opencode_stale_agent.py");
  const captureFile = path.join(runtimeDir, "stale_invocations.jsonl");
  const staleFlag = path.join(runtimeDir, "stale.flag");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, pathlib, sys, time",
      `capture = pathlib.Path(${JSON.stringify(captureFile)})`,
      `stale = pathlib.Path(${JSON.stringify(staleFlag)})`,
      "args = sys.argv[1:]",
      "prompt = args[-1] if args else ''",
      "session = args[args.index('--session') + 1] if '--session' in args else ''",
      "capture.write_text((capture.read_text(encoding='utf-8') if capture.exists() else '') + json.dumps({'argv': args, 'session': session, 'prompt': prompt}, ensure_ascii=False) + '\\n', encoding='utf-8')",
      "if session:",
      "    stale.write_text(session, encoding='utf-8')",
      "    print('No conversation found with session ID ' + session, file=sys.stderr)",
      "    sys.exit(1)",
      "recovered = stale.exists()",
      "thread_id = 'opencode-stale-recovered' if recovered else 'opencode-stale-first'",
      "marker = 'RECOVERED_STALE_SESSION_E2E' if recovered else 'FIRST_STALE_SESSION_E2E'",
      "answer = '## 结论\\n' + marker + ': 已完成本轮 Agent 会话。\\n\\n## 代码证据\\n- `README.md`: 当前工作区证据。\\n- `lib/iscsi/iscsi.c`: login 路径候选。\\n\\n## 黑盒测试用例\\n- 用例：正常登录；前置条件：target 已启动；步骤：initiator 发起 login；预期结果：进入 Full Feature Phase；观测点：响应状态和日志。'",
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
  const workspaceName = `ai-opencode-stale-e2e-${Date.now()}`;
  const runtimeName = `OpenCode stale runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} stale session`;

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

    const composer = page.getByPlaceholder(/像 Codex 一样继续追问/);
    await composer.fill("第一轮：建立 OpenCode 会话");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("FIRST_STALE_SESSION_E2E")).toBeVisible({ timeout: 20_000 });

    await composer.fill("第二轮：沿用会话，如果旧会话失效则自动恢复");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("RECOVERED_STALE_SESSION_E2E")).toBeVisible({ timeout: 30_000 });
    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "No conversation found" })).toHaveCount(0);

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    const staleSessionDetail = processDisclosure.locator("p").filter({ hasText: "旧会话已失效" });
    await expect(staleSessionDetail).toBeHidden();
    await processDisclosure.getByText("Agent 过程").hover();
    await processDisclosure.getByText("Agent 过程").click();
    await expect(staleSessionDetail).toBeVisible();

    const captured = fs.readFileSync(captureFile, "utf8")
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line) as { argv: string[]; session: string; prompt: string });
    expect(captured.map((item) => item.session)).toEqual(["", "opencode-stale-first", ""]);
    expect(captured[2].argv).not.toContain("--session");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    expect(messageBody.items.some((item) => item.role === "assistant" && item.content.includes("RECOVERED_STALE_SESSION_E2E"))).toBe(true);
    expect(messageBody.items.some((item) => item.role === "assistant" && item.content.includes("No conversation found"))).toBe(false);
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
    await request.delete(`${backendBase}/api/workspaces/${encodeURIComponent(workspace.id)}`);
  }
});

test("repairs a thin answer with a fresh session after stale OpenCode resume self-heal", async ({
  page,
  request,
}) => {
  test.setTimeout(80_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-stale-repair-opencode-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "OpenCode stale repair e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-opencode-stale-repair-")));
  const runtimeScript = path.join(runtimeDir, "fake_opencode_stale_repair_agent.py");
  const captureFile = path.join(runtimeDir, "stale_repair_invocations.jsonl");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, pathlib, sys, time",
      `capture = pathlib.Path(${JSON.stringify(captureFile)})`,
      "args = sys.argv[1:]",
      "prompt = args[-1] if args else ''",
      "session = args[args.index('--session') + 1] if '--session' in args else ''",
      "previous = len(capture.read_text(encoding='utf-8').splitlines()) if capture.exists() else 0",
      "capture.write_text((capture.read_text(encoding='utf-8') if capture.exists() else '') + json.dumps({'turn': previous + 1, 'argv': args, 'session': session, 'prompt': prompt}, ensure_ascii=False) + '\\n', encoding='utf-8')",
      "if session:",
      "    print('No conversation found with session ID ' + session, file=sys.stderr)",
      "    sys.exit(1)",
      "if previous == 0:",
      "    answer = '## 结论\\nSTALE_REPAIR_FIRST_SESSION: 已建立 OpenCode 会话。\\n\\n## 代码证据\\n- `README.md`: 当前工作区证据。\\n- `lib/iscsi/iscsi.c`: login 路径候选。\\n\\n## 黑盒测试用例\\n- 用例：正常登录；前置条件：target 已启动；步骤：initiator 发起 login；预期结果：进入 Full Feature Phase；观测点：响应状态和日志。'",
      "    events = [{'type':'thread.started','thread_id':'opencode-stale-repair-first'}, {'type':'message','role':'assistant','content':answer}, {'type':'result','status':'success','thread_id':'opencode-stale-repair-first'}]",
      "elif previous == 2:",
      "    events = [{'type':'message','role':'assistant','content':'你好，有什么需要帮助？'}, {'type':'result','status':'success'}]",
      "else:",
      "    answer = '## 结论\\nSTALE_RESUME_REPAIR_FRESH_E2E: 已在 fresh repair 中完成源码分析。\\n\\n## 代码证据\\n- `README.md`: 当前工作区证据。\\n- `lib/iscsi/iscsi.c`: login 状态机。\\n\\n## 流程梳理\\n1. 旧 OpenCode session resume 失败。\\n2. CodeTalk 丢弃旧 session 并 fresh 重试。\\n3. 薄回答触发 repair，repair 不再带旧 session。\\n\\n## 黑盒测试用例\\n- 用例：正常登录；前置条件：target 已启动；步骤：initiator 发起 login；预期结果：进入 Full Feature Phase；观测点：响应状态和日志。'",
      "    events = [{'type':'message','role':'assistant','content':answer}, {'type':'result','status':'success'}]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "    time.sleep(0.05)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-opencode-stale-repair-e2e-${Date.now()}`;
  const runtimeName = `OpenCode stale repair runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} stale repair`;

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
  const workspace = (await workspaceResp.json()) as { id: string };

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
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);

    const composer = page.getByPlaceholder(/像 Codex 一样继续追问/);
    await composer.fill("第一轮：建立 OpenCode 会话");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("STALE_REPAIR_FIRST_SESSION")).toBeVisible({ timeout: 20_000 });

    await composer.fill("第二轮：沿用会话分析 iSCSI 登录，输出代码证据、流程梳理和黑盒测试用例");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("STALE_RESUME_REPAIR_FRESH_E2E")).toBeVisible({ timeout: 30_000 });
    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "No conversation found" })).toHaveCount(0);
    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "你好，有什么需要帮助" })).toHaveCount(0);

    const captured = fs.readFileSync(captureFile, "utf8")
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line) as { argv: string[]; session: string; prompt: string });
    expect(captured.map((item) => item.session)).toEqual(["", "opencode-stale-repair-first", "", ""]);
    expect(captured[3].argv).not.toContain("--session");
    expect(captured[3].prompt).toContain("上一次执行器输出过短");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    expect(messageBody.items.some((item) => item.role === "assistant" && item.content.includes("STALE_RESUME_REPAIR_FRESH_E2E"))).toBe(true);
    expect(messageBody.items.some((item) => item.role === "assistant" && item.content.includes("No conversation found"))).toBe(false);
    expect(messageBody.items.some((item) => item.role === "assistant" && item.content.includes("你好，有什么需要帮助"))).toBe(false);
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
    await request.delete(`${backendBase}/api/workspaces/${encodeURIComponent(workspace.id)}`);
  }
});

test("repairs a thin OpenCode answer by resuming the latest CLI session", async ({
  page,
  request,
}) => {
  test.setTimeout(80_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-resume-repair-opencode-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "OpenCode resume repair e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-opencode-resume-repair-")));
  const runtimeScript = path.join(runtimeDir, "fake_opencode_resume_repair_agent.py");
  const captureFile = path.join(runtimeDir, "resume_repair_invocations.jsonl");
  fs.writeFileSync(
    runtimeScript,
    [
      "# -*- coding: utf-8 -*-",
      "import json, pathlib, sys, time",
      `capture = pathlib.Path(${JSON.stringify(captureFile)})`,
      "args = sys.argv[1:]",
      "prompt = args[-1] if args else ''",
      "session = args[args.index('--session') + 1] if '--session' in args else ''",
      "previous = len(capture.read_text(encoding='utf-8').splitlines()) if capture.exists() else 0",
      "capture.write_text((capture.read_text(encoding='utf-8') if capture.exists() else '') + json.dumps({'turn': previous + 1, 'argv': args, 'session': session, 'prompt': prompt}, ensure_ascii=False) + '\\n', encoding='utf-8')",
      "if previous == 0:",
      "    events = [",
      "      {'type':'thread.started','thread_id':'opencode-resume-repair-first'},",
      "      {'type':'message','role':'assistant','content':'你好，有什么需要帮助？'},",
      "      {'type':'result','status':'success','thread_id':'opencode-resume-repair-first'},",
      "    ]",
      "else:",
      "    answer = '## 结论\\nOPENCODE_RESUME_REPAIR_E2E: 自动续跑沿用了最新 OpenCode session。\\n\\n## 代码证据\\n- `README.md`: 当前工作区证据。\\n- `lib/nvmf/ctrlr.c`: connect 流程候选。\\n\\n## 流程梳理\\n1. 首次 Agent 建立 CLI session。\\n2. 薄回答触发 CodeTalk repair。\\n3. repair 通过 --session 续接上一次会话，而不是重新初始化。\\n\\n## SFMEA\\n| failure mode | cause | effect | severity | occurrence | detection | RPN | mitigation |\\n| connect timeout | transport delay | 连接失败 | 8 | 3 | 4 | 96 | 增加 timeout 黑盒观测 |\\n\\n## 黑盒测试用例\\n1. 用例：正常连接；前置条件：target 已启动；步骤：initiator 发起 connect；预期结果：连接成功；观测点：状态和日志；失败诊断线索：检查 NQN、listener 和 target 日志。\\n2. 用例：连接超时；前置条件：注入网络延迟；步骤：发起 connect；预期结果：超时失败且可重试；观测点：错误码、日志和重连状态；失败诊断线索：检查延迟注入、重试参数和日志时间线。'",
      "    events = [",
      "      {'type':'thread.started','thread_id':'opencode-resume-repair-second'},",
      "      {'type':'message','role':'assistant','content':answer},",
      "      {'type':'result','status':'success','thread_id':'opencode-resume-repair-second'},",
      "    ]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "    time.sleep(0.05)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-opencode-resume-repair-e2e-${Date.now()}`;
  const runtimeName = `OpenCode resume repair runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} resume repair`;

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
  const workspace = (await workspaceResp.json()) as { id: string };

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
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);

    const composer = page.getByPlaceholder(/像 Codex 一样继续追问/);
    await composer.fill("基于当前源码分析 NVMe-oF connect，输出代码证据、流程梳理、SFMEA，并至少给出两个黑盒测试用例。");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("OPENCODE_RESUME_REPAIR_E2E")).toBeVisible({ timeout: 30_000 });
    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "你好，有什么需要帮助" })).toHaveCount(0);

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    const repairDetail = processDisclosure.locator("p").filter({ hasText: "上一次执行器输出过短" });
    await expect(repairDetail).toBeHidden();
    await processDisclosure.getByText("Agent 过程").hover();
    await processDisclosure.getByText("Agent 过程").click();
    await expect(repairDetail).toBeVisible({ timeout: 15_000 });

    const captured = fs.readFileSync(captureFile, "utf8")
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line) as { argv: string[]; session: string; prompt: string });
    expect(captured.map((item) => item.session)).toEqual(["", "opencode-resume-repair-first"]);
    expect(captured[1].argv).toContain("--session");
    expect(captured[1].prompt).toContain("上一次执行器输出过短");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    expect(messageBody.items.some((item) => item.role === "assistant" && item.content.includes("OPENCODE_RESUME_REPAIR_E2E"))).toBe(true);
    expect(messageBody.items.some((item) => item.role === "assistant" && item.content.includes("你好，有什么需要帮助"))).toBe(false);
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
      "  {'type':'text','timestamp':4,'sessionID':'opencode-native-e2e','part':{'type':'text','text':'## 结论\\nOPENCODE_NATIVE_FINAL: 已基于源码线索完成分析。\\n\\n## 代码证据\\n- `lib/nvmf/ctrlr.c`: `spdk_nvmf_connect` 是 connect 入口候选。\\n- `test/nvmf`: 可承载连接路径回归。\\n\\n## 黑盒测试用例\\n- 用例：正常 connect；前置条件：target 已启动；步骤：initiator 发起连接；预期结果：连接成功；观测点：RPC 状态、日志和连接状态。'}},",
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
    await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0, { timeout: 15_000 });

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
  const cancelledMarker = path.join(runtimeDir, "agent-survived-cancel.txt");
  const childRuntimeScript = path.join(runtimeDir, "slow_agent_child.py");
  const childCancelledMarker = path.join(runtimeDir, "agent-child-survived-cancel.txt");
  fs.writeFileSync(
    childRuntimeScript,
    [
      "import pathlib",
      "import sys",
      "import time",
      "time.sleep(1.5)",
      "pathlib.Path(sys.argv[1]).write_text('agent child survived cancellation', encoding='utf-8')",
      "",
    ].join("\n"),
    "utf8",
  );
  fs.writeFileSync(
    runtimeScript,
    [
      "import pathlib",
      "import subprocess",
      "import sys",
      "import time",
      "sys.stdin.read()",
      `subprocess.Popen([sys.executable, ${JSON.stringify(childRuntimeScript)}, ${JSON.stringify(childCancelledMarker)}])`,
      "print('agent-runtime-first-delta', flush=True)",
      "time.sleep(20)",
      `pathlib.Path(${JSON.stringify(cancelledMarker)}).write_text('agent survived cancellation', encoding='utf-8')`,
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
    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
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
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await processDisclosure.locator("summary").hover();
    await processDisclosure.locator("summary").click();
    await expect(processDisclosure.getByText("CodeTalk 已启动")).toBeVisible();
    await expect(processDisclosure.locator("p").filter({ hasText: "用户已停止本轮 Agent" })).toBeVisible();
    await expect(processDisclosure.getByText("agent-runtime-first-delta")).toHaveCount(0);
    await expect.poll(() => cancelRequests.length).toBe(1);
    await page.waitForTimeout(2_000);
    expect(fs.existsSync(cancelledMarker)).toBe(false);
    expect(fs.existsSync(childCancelledMarker)).toBe(false);

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

    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    const reloadedProcessDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(reloadedProcessDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await reloadedProcessDisclosure.locator("summary").click();
    await expect(reloadedProcessDisclosure.getByText("CodeTalk 已启动")).toBeVisible();
    await expect(
      reloadedProcessDisclosure.locator("p").filter({ hasText: "用户已停止本轮 Agent" }),
    ).toBeVisible();
    await expect(reloadedProcessDisclosure.getByText("agent-runtime-first-delta")).toHaveCount(0);

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

test("continues the same AI thread after cancelling a running agent", async ({
  page,
  request,
}) => {
  test.setTimeout(120_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-cancel-retry-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI cancel retry e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-cancel-retry-")));
  const runtimeScript = path.join(runtimeDir, "cancel_retry_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "# -*- coding: utf-8 -*-",
      "import sys, time",
      "prompt = sys.stdin.read()",
      "if 'RETRY_AFTER_CANCEL' not in prompt:",
      "    print('CANCEL_RETRY_PARTIAL_SHOULD_NOT_PERSIST', flush=True)",
      "    time.sleep(20)",
      "    print('CANCEL_RETRY_AFTER_STOP_SHOULD_NOT_RENDER', flush=True)",
      "else:",
      "    print('## 结论\\nCANCEL_RETRY_FINAL: 取消后同一线程可以继续执行并返回完整答案。\\n\\n## 代码证据\\n- `README.md`: `AI cancel retry e2e workspace` 来自当前工作区。\\n- `test/nvmf`: 可承载取消后重试的黑盒回归。\\n\\n## 流程梳理\\n1. 第一轮 Agent 输出临时片段后被用户点击停止。\\n2. CodeTalk 取消运行并清空 streaming 状态。\\n3. 用户在同一线程继续输入，Agent 重新启动并返回最终答案。\\n\\n## SFMEA\\n| failure mode | cause | effect | severity | occurrence | detection | RPN | mitigation |\\n| 取消后线程卡住 | running 状态未恢复 | 用户无法继续分析 | 8 | 3 | 3 | 72 | 真实 UI 取消后立即发送下一轮并核验消息历史 |\\n\\n## 黑盒测试用例\\n1. 用例：取消后继续输入；前置条件：Agent 正在生成；步骤：点击停止后发送 RETRY_AFTER_CANCEL；预期结果：出现 CANCEL_RETRY_FINAL；观测点：按钮状态、消息历史、运行状态。\\n2. 用例：取消轮不落半截回答；前置条件：第一轮已有临时 delta；步骤：停止并查询消息列表；预期结果：第一轮只有用户消息，没有 assistant；失败诊断线索为 CANCEL_RETRY_PARTIAL_SHOULD_NOT_PERSIST。', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-cancel-retry-e2e-${Date.now()}`;
  const runtimeName = `Cancel retry runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} same thread retry`;

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
      completion_mode: "process_exit",
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
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);

    const firstPrompt = "先启动一个会被取消的长任务";
    await page.getByLabel("AI 线程消息").fill(firstPrompt);
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByRole("button", { name: "停止" })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("CANCEL_RETRY_PARTIAL_SHOULD_NOT_PERSIST")).toBeVisible({ timeout: 20_000 });

    await page.getByRole("button", { name: "停止" }).hover();
    await page.getByRole("button", { name: "停止" }).click();
    await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0, { timeout: 15_000 });
    await expect(page.getByLabel("AI 线程消息")).toBeEnabled({ timeout: 15_000 });
    await expect(page.getByText("CANCEL_RETRY_AFTER_STOP_SHOULD_NOT_RENDER")).toHaveCount(0);

    const retryPrompt = "RETRY_AFTER_CANCEL 请继续同一线程并输出完整四件套";
    await page.getByLabel("AI 线程消息").fill(retryPrompt);
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("CANCEL_RETRY_FINAL")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("CANCEL_RETRY_PARTIAL_SHOULD_NOT_PERSIST")).toHaveCount(0);
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
    expect(messageBody.items.map((item) => item.role)).toEqual(["user", "user", "assistant"]);
    expect(messageBody.items[0].content).toBe(firstPrompt);
    expect(messageBody.items[1].content).toBe(retryPrompt);
    expect(messageBody.items[2].content).toContain("CANCEL_RETRY_FINAL");
    expect(messageBody.items[2].content).not.toContain("CANCEL_RETRY_PARTIAL_SHOULD_NOT_PERSIST");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("lets the user draft the next AI thread prompt while an agent is still running", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-running-draft-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI running draft e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-running-draft-")));
  const runtimeScript = path.join(runtimeDir, "running_draft_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "# -*- coding: utf-8 -*-",
      "import sys, time",
      "prompt = sys.stdin.read()",
      "if 'DRAFT_SECOND_PROMPT' in prompt:",
      "    print('## 结论\\nRUNNING_DRAFT_SECOND_FINAL: 用户运行中起草的下一步已在同一线程继续执行。\\n\\n## 代码证据\\n- `README.md`: `AI running draft e2e workspace` 来自当前工作区。\\n\\n## 黑盒测试用例\\n- 前置条件：第一轮 Agent 已完成；步骤：发送运行中保留的草稿；预期结果：第二轮在同一线程生成。', flush=True)",
      "else:",
      "    print('RUNNING_DRAFT_FIRST_PROGRESS: agent 正在读取源码，用户此时可以起草下一条。', flush=True)",
      "    time.sleep(3)",
      "    print('## 结论\\nRUNNING_DRAFT_FIRST_FINAL: 第一轮完成，草稿不应丢失也不应提前提交。\\n\\n## 代码证据\\n- `README.md`: `AI running draft e2e workspace` 来自当前工作区。', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-running-draft-e2e-${Date.now()}`;
  const runtimeName = `Running draft runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} running draft`;

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
      completion_mode: "process_exit",
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
    const composer = page.getByLabel("AI 线程消息");
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });

    await composer.fill("FIRST_RUNNING_PROMPT 请开始较慢的源码分析");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByRole("button", { name: "停止" })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("RUNNING_DRAFT_FIRST_PROGRESS")).toBeVisible({ timeout: 15_000 });

    const draftPrompt = "DRAFT_SECOND_PROMPT 请沿着上一轮继续生成黑盒测试";
    await expect(composer).toBeEnabled();
    await composer.fill(draftPrompt);
    await expect(composer).toHaveValue(draftPrompt);
    await composer.press("Enter");
    await expect(composer).toHaveValue(draftPrompt);

    const runningMessagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(runningMessagesResp.ok()).toBeTruthy();
    const runningMessages = (await runningMessagesResp.json()) as {
      items: Array<{ role: string; content: string }>;
    };
    expect(runningMessages.items.filter((item) => item.role === "user")).toHaveLength(1);
    expect(runningMessages.items.some((item) => item.content === draftPrompt)).toBe(false);

    await expect(page.getByText("RUNNING_DRAFT_FIRST_FINAL")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: "发送" })).toBeEnabled({ timeout: 15_000 });
    await expect(composer).toHaveValue(draftPrompt);

    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("RUNNING_DRAFT_SECOND_FINAL")).toBeVisible({ timeout: 30_000 });

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as {
      items: Array<{ role: string; content: string }>;
    };
    expect(messageBody.items.map((item) => item.role)).toEqual(["user", "assistant", "user", "assistant"]);
    expect(messageBody.items[2].content).toBe(draftPrompt);
    expect(messageBody.items[3].content).toContain("RUNNING_DRAFT_SECOND_FINAL");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("recovers a running AI thread after browser reload with process state intact", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-reload-running-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI reload running e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-reload-running-")));
  const runtimeScript = path.join(runtimeDir, "reload_running_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "# -*- coding: utf-8 -*-",
      "import sys, time",
      "sys.stdin.read()",
      "print('thinking: RELOAD_PROCESS_STEP_01 reading workspace source README.md', flush=True)",
      "time.sleep(4)",
      "print('## 结论\\nRELOAD_RUNNING_FINAL: 浏览器刷新后，同一轮 Agent 仍能恢复过程状态并显示最终回答。\\n\\n## 代码证据\\n- `README.md`: `AI reload running e2e workspace` 来自当前工作区。\\n\\n## 流程梳理\\n1. 用户在 AI 线程发送较慢的源码分析任务。\\n2. Agent 先输出过程诊断，CodeTalk 将其放入默认折叠的 Agent 过程。\\n3. 浏览器在运行中刷新后重新订阅同一 run，并在完成后刷新线程消息。\\n\\n## 黑盒测试用例\\n- 前置条件：Agent 正在生成；步骤：刷新浏览器页面；预期结果：过程可展开查看，最终回答出现在对话区，发送按钮恢复。', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-reload-running-e2e-${Date.now()}`;
  const runtimeName = `Reload running runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} reload running`;

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
      completion_mode: "process_exit",
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
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);

    await page
      .getByLabel("AI 线程消息")
      .fill("RELOAD_RUNNING_PROMPT 请启动较慢的源码分析，刷新后继续显示过程和最终结果");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByRole("button", { name: "停止" })).toBeVisible({ timeout: 15_000 });

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await processDisclosure.getByText("Agent 过程").click();
    await expect(
      processDisclosure.locator("p").filter({
        hasText: "RELOAD_PROCESS_STEP_01 reading workspace source README.md",
      }),
    ).toBeVisible({ timeout: 15_000 });

    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });

    const restoredProcessDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(restoredProcessDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await restoredProcessDisclosure.getByText("Agent 过程").click();
    await expect(
      restoredProcessDisclosure.locator("p").filter({
        hasText: "RELOAD_PROCESS_STEP_01 reading workspace source README.md",
      }),
    ).toBeVisible({ timeout: 15_000 });

    await expect(page.getByText("RELOAD_RUNNING_FINAL")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0, { timeout: 15_000 });
    const composer = page.getByLabel("AI 线程消息");
    await expect(composer).toBeEnabled({ timeout: 15_000 });
    await composer.fill("刷新恢复后继续追问");
    await expect(page.getByRole("button", { name: "发送" })).toBeEnabled({ timeout: 15_000 });
    const reader = page.getByLabel("AI 线程对话内容");
    await expect(reader).not.toContainText("thinking:");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as {
      items: Array<{ role: string; content: string }>;
    };
    expect(messageBody.items.map((item) => item.role)).toEqual(["user", "assistant"]);
    expect(messageBody.items[1].content).toContain("RELOAD_RUNNING_FINAL");
    expect(messageBody.items[1].content).not.toContain("RELOAD_PROCESS_STEP_01");
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
    expect(Math.abs(scrollTopAfterMoreDeltas - scrollTopWhileReading)).toBeLessThanOrEqual(4);
    expect(distanceFromBottomAfterMoreDeltas).toBeGreaterThan(240);
    await expect(page.getByText("HISTORY-LINE-40")).toBeVisible();
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

test("keeps split terminal OSC noise out of the visible AI thread answer", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-split-osc-repo-")));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "nvmf", "connect.c"),
    "int split_osc_connect_probe(void) { return 0; }\n",
    "utf8",
  );
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-split-osc-")));
  const runtimeScript = path.join(runtimeDir, "split_osc_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys, time",
      "sys.stdin.read()",
      "sys.stdout.write('\\x1b]8;;file:///tmp/nga-session-12345')",
      "sys.stdout.flush()",
      "time.sleep(0.05)",
      "sys.stdout.write('\\x07')",
      "print('## 结论\\nFINAL_SPLIT_OSC_ANSWER: 已过滤分块终端控制噪音，只展示源码分析结论。\\n\\n## 代码证据\\n- `lib/nvmf/connect.c`: `split_osc_connect_probe` 是本轮工作区源码证据。\\n- `test/nvmf`: 可承载 connect/reconnect 黑盒回归。\\n\\n## 流程梳理\\n1. Agent 先输出终端 OSC 链接控制序列。\\n2. CodeTalk 清洗控制噪音后保留最终回答。\\n\\n## SFMEA\\n- failure mode: reconnect timeout; cause: transport delay; effect: I/O pause; severity 8; occurrence 3; detection 4; RPN 96; mitigation: observe RPC error and reconnect state.\\n\\n## 黑盒测试用例\\n1. 用例：正常连接；前置条件：target 已启动；步骤：initiator 发起 connect；预期结果：连接成功；观测点：RPC 状态、日志和连接状态。\\n2. 用例：连接超时；前置条件：注入网络延迟；步骤：发起 connect 并等待超时；预期结果：返回超时错误且可重连；观测点：错误码、日志、恢复状态。', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-split-osc-e2e-${Date.now()}`;
  const runtimeName = `Split OSC runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} terminal cleanup`;
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
      timeout_seconds: 20,
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

    await page.getByLabel("AI 线程消息").fill("分析 connect 路径并输出代码证据、流程、SFMEA 和黑盒测试用例");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.getByText("FINAL_SPLIT_OSC_ANSWER")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("file:///tmp/nga-session-12345")).toHaveCount(0);
    await expect(page.getByText("8;;file:///tmp/nga-session-12345")).toHaveCount(0);

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as {
      items: Array<{ role: string; content: string }>;
    };
    const assistant = [...messageBody.items].reverse().find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("FINAL_SPLIT_OSC_ANSWER");
    expect(assistant?.content).not.toContain("file:///tmp/nga-session-12345");
    expect(assistant?.content).not.toContain("8;;");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("keeps split terminal DCS noise out of the visible AI thread answer", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-split-dcs-repo-")));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "nvmf", "connect.c"),
    "int split_dcs_connect_probe(void) { return 0; }\n",
    "utf8",
  );
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-split-dcs-")));
  const runtimeScript = path.join(runtimeDir, "split_dcs_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys, time",
      "sys.stdin.read()",
      "sys.stdout.write('\\x1bP1;2;3+q54321')",
      "sys.stdout.flush()",
      "time.sleep(0.05)",
      "sys.stdout.write('\\x1b\\\\')",
      "print('## 结论\\nFINAL_SPLIT_DCS_ANSWER: 已过滤 DCS 终端控制噪音，只展示源码分析结论。\\n\\n## 代码证据\\n- `lib/nvmf/connect.c`: `split_dcs_connect_probe` 是本轮工作区源码证据。\\n- `test/nvmf`: 可承载 connect/reconnect 黑盒回归。\\n\\n## 流程梳理\\n1. Agent 先输出 DCS 终端控制序列。\\n2. CodeTalk 清洗控制噪音后保留最终回答。\\n\\n## SFMEA\\n- failure mode: reconnect timeout; cause: transport delay; effect: I/O pause; severity 8; occurrence 3; detection 4; RPN 96; mitigation: observe RPC error and reconnect state.\\n\\n## 黑盒测试用例\\n1. 用例：正常连接；前置条件：target 已启动；步骤：initiator 发起 connect；预期结果：连接成功；观测点：RPC 状态、日志和连接状态。\\n2. 用例：连接超时；前置条件：注入网络延迟；步骤：发起 connect 并等待超时；预期结果：返回超时错误且可重连；观测点：错误码、日志、恢复状态。', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-split-dcs-e2e-${Date.now()}`;
  const runtimeName = `Split DCS runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} terminal cleanup`;
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
      timeout_seconds: 20,
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

    await page.getByLabel("AI 线程消息").fill("分析 connect 路径并输出代码证据、流程、SFMEA 和黑盒测试用例");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.getByText("FINAL_SPLIT_DCS_ANSWER")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("1;2;3+q54321")).toHaveCount(0);
    await expect(page.getByText("54321")).toHaveCount(0);

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as {
      items: Array<{ role: string; content: string }>;
    };
    const assistant = [...messageBody.items].reverse().find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("FINAL_SPLIT_DCS_ANSWER");
    expect(assistant?.content).not.toContain("1;2;3+q54321");
    expect(assistant?.content).not.toContain("54321");
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
      "print('## 结论\\nFINAL_DIAGNOSTIC_ANSWER: black-box reconnect timeout should observe RPC error, log, and state recovery.\\n\\n## 代码证据\\n- `README.md`: `AI diagnostic folding e2e workspace` 来自当前工作区。\\n- `lib/nvmf/connect.c`: 作为本轮 connect/reconnect 证据域，过程细节只进折叠 Agent 过程。\\n\\n## 黑盒测试用例\\n- 用例 1：前置条件为 NVMe-oF target 可连接；步骤为触发 reconnect timeout；预期结果为 RPC 返回超时错误、日志记录恢复动作、状态可重新连接；观测点为 RPC 响应、日志、连接状态。\\n- 用例 2：前置条件为已有队列；步骤为断开后重连；预期结果为 I/O 不重复提交，失败诊断线索包含连接状态变化。', flush=True)",
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
    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    const processSummary = processDisclosure.locator("summary");
    await expect(processSummary).toContainText("最新：内部过程已更新，可展开查看");
    await expect(processSummary).not.toContainText("chain-of-thought");
    await expect(processSummary).not.toContainText("internal note");
    await expect(processSummary).not.toContainText("internal multiline note");
    await expect(processDisclosure.getByText("reading workspace source evidence")).toBeHidden();
    await expect(processDisclosure.getByText("internal multiline note: select evidence cards")).toBeHidden();
    await expect(processDisclosure.getByText("internal multiline note: avoid exposing")).toBeHidden();
    await expect(processDisclosure.getByText("chain-of-thought-like internal note")).toBeHidden();

    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("reading workspace source evidence")).toBeVisible();
    await expect(processDisclosure.getByText("internal multiline note: select evidence cards")).toBeVisible();
    await expect(processDisclosure.getByText("internal multiline note: avoid exposing")).toBeVisible();
    await expect(processDisclosure.getByText("chain-of-thought-like internal note")).toBeVisible();
    await expect(page.getByText("生成诊断：默认折叠")).toHaveCount(0);
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

    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("FINAL_DIAGNOSTIC_ANSWER")).toBeVisible({ timeout: 15_000 });
    const reloadedReader = page.getByLabel("AI 线程对话内容");
    await expect(reloadedReader).not.toContainText("reading workspace source evidence");
    await expect(reloadedReader).not.toContainText("internal multiline note");
    await expect(reloadedReader).not.toContainText("chain-of-thought-like internal note");

    const reloadedProcessDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(reloadedProcessDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect
      .poll(async () => reloadedProcessDisclosure.evaluate((node) => (node as HTMLDetailsElement).open))
      .toBe(false);
    await expect(reloadedProcessDisclosure.getByText("reading workspace source evidence")).toBeHidden();
    await reloadedProcessDisclosure.getByText("Agent 过程").click();
    await expect(reloadedProcessDisclosure.getByText("reading workspace source evidence")).toBeVisible();
    await expect(reloadedProcessDisclosure.getByText("internal multiline note: select evidence cards")).toBeVisible();
    await expect(reloadedProcessDisclosure.getByText("chain-of-thought-like internal note")).toBeVisible();
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("shows real agent stderr progress in the folded process panel while generation is running", async ({
  page,
  request,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-stderr-progress-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI stderr progress e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-stderr-runtime-")));
  const runtimeScript = path.join(runtimeDir, "stderr_progress_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys, time",
      "sys.stdin.read()",
      "sys.stderr.write('stderr progress: reading workspace source lib/nvmf/connect.c\\n'); sys.stderr.flush()",
      "time.sleep(1.2)",
      "sys.stderr.write('stderr progress: mapping SFMEA and black-box tests\\n'); sys.stderr.flush()",
      "time.sleep(0.2)",
      "print('## 结论\\nSTDERR_PROGRESS_FINAL: 已基于源码完成分析。\\n\\n## 代码证据\\n- `README.md`: `AI stderr progress e2e workspace`。\\n- `lib/nvmf/connect.c`: 作为 connect 流程证据域。\\n\\n## 黑盒测试用例\\n- 用例：前置条件为 target 可连接；步骤为触发 reconnect timeout；预期结果为日志和状态可观测。', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-stderr-progress-${Date.now()}`;
  const runtimeName = `Stderr progress runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} folded stderr progress`;

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
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);

    await page.getByPlaceholder(/像 Codex 一样继续追问/).fill("请读取源码并生成 SFMEA 与黑盒测试用例");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 10_000 });
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("stderr progress: reading workspace source lib/nvmf/connect.c")).toBeVisible({
      timeout: 10_000,
    });
    const reader = page.getByLabel("AI 线程对话内容");
    await expect(reader).not.toContainText("stderr progress: reading workspace source");

    await expect(page.getByText("STDERR_PROGRESS_FINAL")).toBeVisible({ timeout: 30_000 });
    await expect(processDisclosure.getByText("stderr progress: mapping SFMEA and black-box tests")).toBeVisible();
    await expect(reader).not.toContainText("stderr progress: mapping SFMEA");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as { items: Array<{ role: string; content: string }> };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("STDERR_PROGRESS_FINAL");
    expect(assistant?.content).not.toContain("stderr progress: reading workspace source");
    expect(assistant?.content).not.toContain("stderr progress: mapping SFMEA");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("folds real agent log and progress JSON events out of the visible answer", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-log-progress-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI log progress folding e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-log-progress-")));
  const runtimeScript = path.join(runtimeDir, "log_progress_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json, sys, time",
      "sys.stdin.read()",
      "answer = '## 结论\\nFINAL_LOG_PROGRESS_ANSWER 已基于源码完成 connect 黑盒分析。\\n\\n## 代码证据\\n- `README.md`: `AI log progress folding e2e workspace` 来自当前工作区。\\n- `lib/nvmf/ctrlr.c`: connect 路径证据。\\n\\n## 黑盒测试用例\\n- 用例：正常连接；前置条件：target 已启动；步骤：发起 connect；预期结果：连接成功；观测点：RPC 状态、日志和连接状态。'",
      "events = [",
      "  {'type': 'log', 'message': '正在读取 lib/nvmf/ctrlr.c，已处理 12/100'},",
      "  {'event': 'progress', 'data': {'message': '扫描 lib/bdev，命中 47 条候选'}},",
      "  {'kind': 'warning', 'message': '工具返回了非关键告警'},",
      "  {'type': 'result', 'status': 'success', 'result': answer},",
      "]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "    time.sleep(0.05)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-log-progress-e2e-${Date.now()}`;
  const runtimeName = `Log progress runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} folded log progress`;

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
      timeout_seconds: 30,
      enabled: true,
      completion_mode: "process_exit",
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
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);

    await page.getByLabel("AI 线程消息").fill("分析 SPDK connect 黑盒测试，不要把 agent 日志混入答案");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.getByText("FINAL_LOG_PROGRESS_ANSWER")).toBeVisible({ timeout: 30_000 });
    const reader = page.getByLabel("AI 线程对话内容");
    await expect(reader).not.toContainText("正在读取 lib/nvmf/ctrlr.c");
    await expect(reader).not.toContainText("扫描 lib/bdev");
    await expect(reader).not.toContainText("工具返回了非关键告警");

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect(processDisclosure.getByText("正在读取 lib/nvmf/ctrlr.c")).toBeHidden();
    await expect(processDisclosure.getByText("扫描 lib/bdev")).toBeHidden();
    await processDisclosure.getByText("Agent 过程").hover();
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("正在读取 lib/nvmf/ctrlr.c")).toBeVisible();
    await expect(processDisclosure.getByText("扫描 lib/bdev")).toBeVisible();
    await expect(processDisclosure.getByText("工具返回了非关键告警", { exact: true })).toBeVisible();

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as {
      items: Array<{ role: string; content: string }>;
    };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("FINAL_LOG_PROGRESS_ANSWER");
    expect(assistant?.content).not.toContain("正在读取 lib/nvmf/ctrlr.c");
    expect(assistant?.content).not.toContain("扫描 lib/bdev");
    expect(assistant?.content).not.toContain("工具返回了非关键告警");
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

test("shows collapsed Agent process progress while keeping diagnostics out of the answer", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-process-summary-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI process collapsed summary e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-process-summary-agent-")));
  const runtimeScript = path.join(runtimeDir, "process_summary_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys, time",
      "sys.stdin.read()",
      "for index in range(1, 5):",
      "    print(f'thinking: COLLAPSED_PROGRESS_STEP_{index:02d} reading workspace source evidence', flush=True)",
      "    time.sleep(0.8)",
      "print('## 结论', flush=True)",
      "print('COLLAPSED_PROGRESS_FINAL: 已完成源码分析，过程保持折叠但有进度提示。', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-process-summary-e2e-${Date.now()}`;
  const runtimeName = `Process summary runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} process summary`;

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
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });

    await page.getByLabel("AI 线程消息").fill("PROCESS_SUMMARY_RUN 请分析源码，并用折叠过程显示进展");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect
      .poll(async () => processDisclosure.evaluate((node) => (node as HTMLDetailsElement).open))
      .toBe(false);
    await expect(processDisclosure.locator("summary")).toContainText("COLLAPSED_PROGRESS_STEP_01", {
      timeout: 15_000,
    });
    await expect(processDisclosure.locator("summary")).toContainText("COLLAPSED_PROGRESS_STEP_04", {
      timeout: 20_000,
    });
    await expect
      .poll(async () => processDisclosure.evaluate((node) => (node as HTMLDetailsElement).open))
      .toBe(false);
    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "COLLAPSED_PROGRESS_STEP_04" })).toHaveCount(0);

    await expect(page.getByText("COLLAPSED_PROGRESS_FINAL")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0, { timeout: 15_000 });
    await processDisclosure.getByText("Agent 过程").hover();
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("COLLAPSED_PROGRESS_STEP_04")).toBeVisible();
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("clears the previous Agent process when a new turn starts in the same thread", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-process-turns-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI process turn isolation e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-process-turns-agent-")));
  const runtimeScript = path.join(runtimeDir, "process_turns_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys, time",
      "prompt = sys.stdin.read()",
      "if 'SECOND_TURN_PROCESS_RUN' in prompt:",
      "    for index in range(1, 5):",
      "        print(f'thinking: SECOND_TURN_PROCESS_STEP_{index:02d} reading current task evidence', flush=True)",
      "        time.sleep(0.8)",
      "    print('## 结论', flush=True)",
      "    print('SECOND_TURN_PROCESS_FINAL: 第二轮只展示当前任务过程。', flush=True)",
      "else:",
      "    for index in range(1, 4):",
      "        print(f'thinking: FIRST_TURN_PROCESS_STEP_{index:02d} reading previous task evidence', flush=True)",
      "        time.sleep(0.2)",
      "    print('## 结论', flush=True)",
      "    print('FIRST_TURN_PROCESS_FINAL: 第一轮完成，过程稍后不应污染第二轮。', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-process-turns-e2e-${Date.now()}`;
  const runtimeName = `Process turns runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} process turns`;

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
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });

    await page.getByLabel("AI 线程消息").fill("FIRST_TURN_PROCESS_RUN 请分析第一轮任务");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("FIRST_TURN_PROCESS_FINAL")).toBeVisible({ timeout: 20_000 });

    const firstProcessDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(firstProcessDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await firstProcessDisclosure.getByText("Agent 过程").click();
    await expect(firstProcessDisclosure.getByText("FIRST_TURN_PROCESS_STEP_03")).toBeVisible();

    await page.getByLabel("AI 线程消息").fill("SECOND_TURN_PROCESS_RUN 请继续第二轮任务，只显示当前过程");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("FIRST_TURN_PROCESS_STEP_03")).toHaveCount(0);

    const secondProcessDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(secondProcessDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect(secondProcessDisclosure.locator("summary")).not.toContainText("FIRST_TURN_PROCESS_STEP");
    await expect(secondProcessDisclosure.locator("summary")).toContainText("SECOND_TURN_PROCESS_STEP_01", {
      timeout: 15_000,
    });
    await expect(secondProcessDisclosure.locator("summary")).toContainText("SECOND_TURN_PROCESS_STEP_04", {
      timeout: 20_000,
    });
    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "FIRST_TURN_PROCESS_STEP" })).toHaveCount(0);
    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "SECOND_TURN_PROCESS_STEP" })).toHaveCount(0);

    await expect(page.getByText("SECOND_TURN_PROCESS_FINAL")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0, { timeout: 15_000 });
    const secondProcessOpen = await secondProcessDisclosure.evaluate((node) => (node as HTMLDetailsElement).open);
    if (!secondProcessOpen) {
      await secondProcessDisclosure.locator("summary").hover();
      await secondProcessDisclosure.locator("summary").click();
    }
    await expect
      .poll(async () => secondProcessDisclosure.evaluate((node) => (node as HTMLDetailsElement).open))
      .toBe(true);
    await expect(secondProcessDisclosure.locator("p").filter({ hasText: "SECOND_TURN_PROCESS_STEP_04" })).toBeVisible();
    await expect(secondProcessDisclosure.getByText("FIRST_TURN_PROCESS_STEP_03")).toHaveCount(0);
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
      "sys.stdout.write('Welcome to Claude Code\\n')",
      "sys.stdout.write('Ready for your next task.\\n')",
      "sys.stdout.write('Tip: press Ctrl+C to stop generation\\n')",
      "sys.stdout.write('│ Session ready                │\\n')",
      "sys.stdout.write('47%\\n12/100\\n')",
      "sys.stdout.buffer.write(bytes([0x80, 0x81, 0x8D, 0x90, 0x9D]) + b'\\n')",
      "sys.stdout.flush()",
      "sys.stdout.write('\\r\\x1b[2K⠋ 12\\r\\x1b[2K⠙ 47\\r\\x1b[2K\\x1b(B')",
      "sys.stdout.flush()",
      "sys.stdout.buffer.write('源码证据：连接失败\\n'.encode('gbk'))",
      "sys.stdout.write('## 结论\\nFINAL_NOISE_CLEAN_ANSWER: 已完成源码分析。\\n\\n## 代码证据\\n- `README.md`: `AI terminal noise e2e workspace` 表明 Agent 读取了当前工作区。\\n- `lib/nvmf`: 可作为存储链路噪声清洗回归的源码域。\\n\\n## 流程梳理\\n1. 外部 Agent 启动时输出欢迎、ready、tip 和进度噪声。\\n2. CodeTalk 清洗终端噪声，仅保留用户需要看的源码分析结论。\\n')",
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
    await expect(page.locator("body")).not.toContainText("Welcome to Claude Code");
    await expect(page.locator("body")).not.toContainText("Ready for your next task");
    await expect(page.locator("body")).not.toContainText("Tip: press Ctrl+C");
    await expect(page.locator("body")).not.toContainText("Session ready");
    await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0, { timeout: 15_000 });

    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("FINAL_NOISE_CLEAN_ANSWER")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("源码证据：连接失败")).toBeVisible();
    await expect(page.locator("body")).not.toContainText("47%");
    await expect(page.locator("body")).not.toContainText("12/100");
    await expect(page.locator("body")).not.toContainText("(B");
    await expect(page.locator("body")).not.toContainText("�");
    await expect(page.locator("body")).not.toContainText("Welcome to Claude Code");
    await expect(page.locator("body")).not.toContainText("Ready for your next task");
    await expect(page.locator("body")).not.toContainText("Tip: press Ctrl+C");
    await expect(page.locator("body")).not.toContainText("Session ready");

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
    expect(assistant?.content).not.toContain("Welcome to Claude Code");
    expect(assistant?.content).not.toContain("Ready for your next task");
    expect(assistant?.content).not.toContain("Tip: press Ctrl+C");
    expect(assistant?.content).not.toContain("Session ready");

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
    expect(exported).not.toContain("Welcome to Claude Code");
    expect(exported).not.toContain("Ready for your next task");
    expect(exported).not.toContain("Tip: press Ctrl+C");
    expect(exported).not.toContain("Session ready");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("cleans external-agent usage banners before display, persistence, and export", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-help-banner-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI help banner cleanup e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-help-banner-")));
  const runtimeScript = path.join(runtimeDir, "help_banner_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "sys.stdin.read()",
      "sys.stdout.write('Usage: claude [options] [prompt]\\n')",
      "sys.stdout.write('Options:\\n')",
      "sys.stdout.write('  --print            Print response\\n')",
      "sys.stdout.write('  --output-format    stream-json\\n')",
      "sys.stdout.write('Model: claude-sonnet-4\\n')",
      "sys.stdout.write('Context: /Volumes/Media/dpdk/spdk\\n')",
      "sys.stdout.write('## 结论\\nFINAL_HELP_BANNER_CLEAN_ANSWER: 已完成源码分析。\\n\\n## 代码证据\\n- `README.md`: `AI help banner cleanup e2e workspace` 表明 Agent 读取了当前工作区。\\n- `lib/nvmf`: 可作为 connect 流程证据域。\\n\\n## 黑盒测试用例\\n- 前置条件：target 已启动；步骤：运行 `spdk_tgt --wait-for-rpc` 后发起 connect；预期结果：连接成功或返回明确错误；观测点：RPC 状态、日志和连接状态。\\n')",
      "sys.stdout.flush()",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-help-banner-e2e-${Date.now()}`;
  const runtimeName = `Help banner runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} help banner cleanup`;

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
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);

    await page.getByLabel("AI 线程消息").fill("HELP_BANNER_CLEAN_RUN 请读取源码并输出最终分析");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("FINAL_HELP_BANNER_CLEAN_ANSWER")).toBeVisible({ timeout: 30_000 });
    const reader = page.getByLabel("AI 线程对话内容");
    await expect(reader).toContainText("spdk_tgt --wait-for-rpc");
    await expect(reader).not.toContainText("Usage: claude");
    await expect(reader).not.toContainText("Options:");
    await expect(reader).not.toContainText("--print");
    await expect(reader).not.toContainText("--output-format");
    await expect(reader).not.toContainText("Model: claude-sonnet-4");
    await expect(reader).not.toContainText("Context: /Volumes/Media/dpdk/spdk");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as {
      items: Array<{ role: string; content: string }>;
    };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("FINAL_HELP_BANNER_CLEAN_ANSWER");
    expect(assistant?.content).toContain("spdk_tgt --wait-for-rpc");
    expect(assistant?.content).not.toContain("Usage: claude");
    expect(assistant?.content).not.toContain("Options:");
    expect(assistant?.content).not.toContain("--print");
    expect(assistant?.content).not.toContain("--output-format");
    expect(assistant?.content).not.toContain("Model: claude-sonnet-4");
    expect(assistant?.content).not.toContain("Context: /Volumes/Media/dpdk/spdk");

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出" }).hover();
    await page.getByRole("button", { name: "导出" }).click();
    const download = await downloadPromise;
    const exportPath = testInfo.outputPath("real-ai-thread-help-banner-clean-export.md");
    await download.saveAs(exportPath);
    const exported = fs.readFileSync(exportPath, "utf8");
    expect(exported).toContain("FINAL_HELP_BANNER_CLEAN_ANSWER");
    expect(exported).toContain("spdk_tgt --wait-for-rpc");
    expect(exported).not.toContain("Usage: claude");
    expect(exported).not.toContain("Options:");
    expect(exported).not.toContain("--print");
    expect(exported).not.toContain("--output-format");
    expect(exported).not.toContain("Model: claude-sonnet-4");
    expect(exported).not.toContain("Context: /Volumes/Media/dpdk/spdk");
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
      "    {'type': 'text', 'text': '## 结论\\nFINAL_JSON_PARTS_ANSWER: 只展示源码分析结论。\\n\\n## 代码证据\\n- `README.md`: `AI JSON part folding e2e workspace` 来自当前工作区。\\n- `lib/nvmf/connect.c`: 作为源码证据域，工具过程应折叠而不进入正文。\\n\\n## 流程梳理\\n1. Agent 先产生 thinking/tool_result 过程事件。\\n2. CodeTalk 只把最终 text 作为回答展示。\\n\\n## 黑盒测试用例\\n- 用例 1：前置条件为工作区已选择；步骤为让 Agent 输出混合 JSON part；预期结果为正文只展示最终答案，观测点为对话区文本。\\n- 用例 2：前置条件为 Agent 过程存在；步骤为展开 Agent 过程；预期结果为内部工具事件仅在折叠区可见，失败诊断线索为正文污染。'},",
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
    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect(processDisclosure.getByText("内部推理：先列出工具计划")).toBeHidden();
    await expect(processDisclosure.getByText("cat /secret/path returned internal-only trace")).toBeHidden();

    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("内部推理：先列出工具计划")).toBeVisible();
    await expect(processDisclosure.getByText("cat /secret/path returned internal-only trace")).toBeVisible();
    await expect(page.getByText("生成诊断：默认折叠")).toHaveCount(0);

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

test("folds split agent thinking and source output while keeping process expandable", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-split-thinking-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI split thinking e2e workspace\n", "utf8");
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-split-thinking-")));
  const runtimeScript = path.join(runtimeDir, "split_thinking_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import json",
      "import sys",
      "sys.stdin.read()",
      "events = [",
      "  {'content': 'THINKING: '},",
      "  {'content': '我先核对工作区 iSCSI 登录相关源码，再'},",
      "  {'content': '据此设计黑盒用例。'},",
      "  {'content': 'Bash {\"command\": \"grep -n login lib/iscsi/iscsi.c | head -60\"}'},",
      "  {'content': '1125:iscsi_conn_login_pdu_success_complete(void *arg)\\n'},",
      "  {'content': 'lib/iscsi/iscsi.c:1539:\\t\\trc = iscsi_op_login_update_param(conn, \"AuthMethod\", \"CHAP\", \"CHAP\");\\n'},",
      "  {'content': 'THINKING: '},",
      "  {'content': '我已掌'},",
      "  {'content': '握登录处理链的关键分支。下面基于 `lib/iscsi/iscsi.c` 的实际校验逻辑给出黑盒用例'},",
      "  {'content': '。\\n'},",
      "  {'content': '## 黑盒测试用例\\n'},",
      "  {'content': '### TC-01 正常登录\\n'},",
      "  {'content': '前置条件：target 已启动；步骤：initiator 发起 login；预期结果：进入 Full Feature Phase。\\n'},",
      "]",
      "for event in events:",
      "    print(json.dumps(event, ensure_ascii=False), flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-split-thinking-e2e-${Date.now()}`;
  const runtimeName = `Split thinking runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} split thinking`;
  const prompt = "SPLIT_THINKING_RUN 针对 iSCSI 登录写几个黑盒用例，过程不要混进正文";

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

    await expect(page.getByText("TC-01 正常登录")).toBeVisible({ timeout: 30_000 });
    const reader = page.getByLabel("AI 线程对话内容");
    await expect(reader).toContainText("我已掌握登录处理链的关键分支");
    await expect(reader).not.toContainText("我先核对工作区");
    await expect(reader).not.toContainText("Bash");
    await expect(reader).not.toContainText("iscsi_conn_login_pdu_success_complete");
    await expect(reader).not.toContainText("AuthMethod");

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect(processDisclosure.getByText("最新：。")).toHaveCount(0);
    await expect(processDisclosure.getByText("最新：我已掌")).toHaveCount(0);
    await expect(processDisclosure.getByText("我先核对工作区")).toBeHidden();
    await processDisclosure.getByText("Agent 过程").hover();
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("我先核对工作区")).toBeVisible();
    await expect(processDisclosure.getByText("iscsi_conn_login_pdu_success_complete")).toBeVisible();
    await expect(processDisclosure.getByText("我已掌握登录处理链")).toHaveCount(0);

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as {
      items: Array<{ role: string; content: string }>;
    };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("TC-01 正常登录");
    expect(assistant?.content).toContain("我已掌握登录处理链的关键分支");
    expect(assistant?.content).not.toContain("我先核对工作区");
    expect(assistant?.content).not.toContain("Bash");
    expect(assistant?.content).not.toContain("AuthMethod");

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出" }).hover();
    await page.getByRole("button", { name: "导出" }).click();
    const download = await downloadPromise;
    const exportPath = testInfo.outputPath("real-ai-thread-split-thinking-export.md");
    await download.saveAs(exportPath);
    const exported = fs.readFileSync(exportPath, "utf8");
    expect(exported).toContain("TC-01 正常登录");
    expect(exported).not.toContain("我先核对工作区");
    expect(exported).not.toContain("Bash");
    expect(exported).not.toContain("AuthMethod");
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

test("fails visibly when a structured agent answer still lacks required sections after repair", async ({
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
  const threadTitle = `${workspaceName} structured quality failure`;
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
    await expect(page.locator("div[role='alert']").filter({ hasText: "Agent 返回内容不足" })).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.locator("div[role='alert']").filter({ hasText: "缺失的证据、SFMEA、流程梳理和黑盒测试用例" })).toBeVisible();
    await expect(page.getByRole("button", { name: "重试上一条" })).toBeVisible();
    await expect(page.getByText("QUALITY_WARNING_VISIBLE_ANSWER")).toHaveCount(0);

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect
      .poll(async () => processDisclosure.evaluate((node) => (node as HTMLDetailsElement).open))
      .toBe(false);
    await processDisclosure.getByText("Agent 过程").click();
    await expect
      .poll(async () => processDisclosure.evaluate((node) => (node as HTMLDetailsElement).open))
      .toBe(true);
    await expect(processDisclosure.getByText("上一次执行器输出过短")).toBeVisible();
    await expect(page.getByText("生成诊断：默认折叠")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0, { timeout: 15_000 });

    const conversationResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}`,
    );
    expect(conversationResp.ok()).toBeTruthy();
    const conversation = (await conversationResp.json()) as {
      status: string;
      latest_run: { status: string } | null;
    };
    expect(conversation.status).toBe("error");
    expect(conversation.latest_run?.status).toBe("failed");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as {
      items: Array<{ role: string; content: string }>;
    };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content ?? "").not.toContain("QUALITY_WARNING_VISIBLE_ANSWER");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("fails visibly when an agent returns only one case for a two-case black-box task", async ({
  page,
  request,
}) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-case-breadth-repo-")));
  fs.mkdirSync(path.join(repo, "lib", "iscsi"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "iscsi", "iscsi.c"),
    "int iscsi_login_case_breadth_probe(void) { return 0; }\n",
    "utf8",
  );
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-case-breadth-")));
  const runtimeScript = path.join(runtimeDir, "case_breadth_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "sys.stdin.read()",
      "print('## 结论\\nCASE_BREADTH_INCOMPLETE_ANSWER: 只生成了一条测试用例。\\n\\n## 代码证据\\n- `lib/iscsi/iscsi.c`: `iscsi_login_case_breadth_probe` 是本轮源码证据。\\n- `test/iscsi_tgt`: 可承载 iSCSI login 黑盒回归。\\n\\n## 黑盒测试用例\\n### TC-01 正常登录\\n前置条件：target 已启动；步骤：initiator 发起 iSCSI Login；预期结果：进入 Full Feature Phase。\\n观测点：Login Response status class/detail、session state、target 日志。\\n失败诊断线索：若状态异常，检查 CHAP 配置、InitiatorName 和 target 日志。', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-case-breadth-e2e-${Date.now()}`;
  const runtimeName = `Case breadth runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} two case guard`;
  const prompt = "针对 iSCSI login 写两个黑盒测试用例，先读源码证据，并包含前置条件、步骤、预期结果、观测点和失败诊断线索";

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
    await expect(page.locator("div[role='alert']").filter({ hasText: "Agent 返回内容不足" })).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByRole("button", { name: "重试上一条" })).toBeVisible();
    await expect(page.getByText("CASE_BREADTH_INCOMPLETE_ANSWER")).toHaveCount(0);

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("上一次执行器输出过短")).toBeVisible();

    const conversationResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}`,
    );
    expect(conversationResp.ok()).toBeTruthy();
    const conversation = (await conversationResp.json()) as {
      status: string;
      latest_run: { status: string } | null;
    };
    expect(conversation.status).toBe("error");
    expect(conversation.latest_run?.status).toBe("failed");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as {
      items: Array<{ role: string; content: string }>;
    };
    const assistant = messageBody.items.find((item) => item.role === "assistant");
    expect(assistant?.content ?? "").not.toContain("CASE_BREADTH_INCOMPLETE_ANSWER");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("retries a structured quality failure and recovers with a complete agent answer", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-quality-retry-repo-")));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "nvmf", "ctrlr.c"),
    "int nvmf_ctrlr_connect_retry_quality(void) { return 0; }\n",
    "utf8",
  );
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-quality-retry-")));
  const runtimeScript = path.join(runtimeDir, "quality_retry_agent.py");
  const counterFile = path.join(runtimeDir, "invocations.txt");
  fs.writeFileSync(
    runtimeScript,
    [
      "import pathlib, sys",
      "counter = pathlib.Path(__file__).with_name('invocations.txt')",
      "prompt = sys.stdin.read()",
      "count = int(counter.read_text(encoding='utf-8') or '0') if counter.exists() else 0",
      "count += 1",
      "counter.write_text(str(count), encoding='utf-8')",
      "if count < 3:",
      "    print('QUALITY_RETRY_INCOMPLETE_ANSWER', flush=True)",
      "    print('Evidence: lib/nvmf/ctrlr.c nvmf_ctrlr_connect_retry_quality', flush=True)",
      "    print('Flow: connect request -> controller setup -> IO queue ready', flush=True)",
      "else:",
      "    print('## 结论', flush=True)",
      "    print('QUALITY_RETRY_FINAL_ANSWER: 已基于源码完成完整四件套。', flush=True)",
      "    print('\\n## 代码证据', flush=True)",
      "    print('- `lib/nvmf/ctrlr.c`: `nvmf_ctrlr_connect_retry_quality` 是本轮 connect 入口证据。', flush=True)",
      "    print('- `test/nvmf`: 可承载 connect/reconnect 黑盒回归。', flush=True)",
      "    print('\\n## 流程梳理', flush=True)",
      "    print('1. Initiator 发起 NVMe-oF connect。', flush=True)",
      "    print('2. Target 建立 controller 并完成 queue 准备。', flush=True)",
      "    print('\\n## SFMEA', flush=True)",
      "    print('- failure mode: reconnect timeout; cause: transport delay; effect: I/O pause; severity 8; occurrence 3; detection 4; RPN 96; mitigation: observe RPC error and reconnect state.', flush=True)",
      "    print('\\n## 黑盒测试用例', flush=True)",
      "    print('1. 用例：正常连接；前置条件：target 已启动；步骤：initiator 发起 connect；预期结果：连接成功；观测点：RPC 状态、日志和连接状态。', flush=True)",
      "    print('2. 用例：连接超时；前置条件：注入网络延迟；步骤：发起 connect 并等待超时；预期结果：返回超时错误且可重连；观测点：错误码、日志、恢复状态。', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-quality-retry-e2e-${Date.now()}`;
  const runtimeName = `Quality retry runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} retry quality`;
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
    const retryButton = page.getByRole("button", { name: "重试上一条" });
    await expect(page.locator("div[role='alert']").filter({ hasText: "Agent 返回内容不足" })).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText("QUALITY_RETRY_INCOMPLETE_ANSWER")).toHaveCount(0);

    await retryButton.hover();
    await retryButton.click();
    await expect(page.getByText("QUALITY_RETRY_FINAL_ANSWER")).toBeVisible({ timeout: 30_000 });
    await expect(page.locator("div[role='alert']").filter({ hasText: "Agent 返回内容不足" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0, { timeout: 15_000 });
    await expect(page.getByRole("link", { name: "下载完整产物" })).toBeVisible({ timeout: 15_000 });
    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "完整测试设计/SFMEA/黑盒用例已保存为下载产物" })).toBeVisible();

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "下载完整产物" }).hover();
    await page.getByRole("link", { name: "下载完整产物" }).click();
    const download = await downloadPromise;
    const artifactPath = testInfo.outputPath("quality-retry-artifact.md");
    await download.saveAs(artifactPath);
    const artifact = fs.readFileSync(artifactPath, "utf8");
    expect(artifact).toContain("QUALITY_RETRY_FINAL_ANSWER");
    expect(artifact).toContain("## 代码证据");
    expect(artifact).toContain("## 流程梳理");
    expect(artifact).toContain("## SFMEA");
    expect(artifact).toContain("## 黑盒测试用例");
    expect(artifact).not.toContain("QUALITY_RETRY_INCOMPLETE_ANSWER");

    const processDisclosure = page.getByTestId("agent-process-disclosure");
    await expect(processDisclosure.getByText("Agent 过程")).toBeVisible({ timeout: 15_000 });
    await expect
      .poll(async () => processDisclosure.evaluate((node) => (node as HTMLDetailsElement).open))
      .toBe(false);
    await processDisclosure.getByText("Agent 过程").click();
    await expect(processDisclosure.getByText("GitNexus/CGC 图谱产物未命中")).toBeVisible();
    await expect(processDisclosure.getByText("QUALITY_RETRY_INCOMPLETE_ANSWER")).toHaveCount(0);

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
    const assistant = [...messageBody.items].reverse().find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("QUALITY_RETRY_FINAL_ANSWER");
    expect(assistant?.content).toContain("下载完整产物");
    expect(assistant?.content).not.toContain("## SFMEA");
    expect(assistant?.content).not.toContain("## 黑盒测试用例");
    expect(assistant?.content).not.toContain("QUALITY_RETRY_INCOMPLETE_ANSWER");
    expect(fs.readFileSync(counterFile, "utf8")).toBe("3");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
  }
});

test("downloads only the latest successful artifact after an agent retry", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-stale-artifact-retry-repo-")));
  fs.mkdirSync(path.join(repo, "lib", "iscsi"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "iscsi", "iscsi.c"),
    "int iscsi_login_retry_artifact_probe(void) { return 0; }\n",
    "utf8",
  );
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-stale-artifact-retry-")));
  const runtimeScript = path.join(runtimeDir, "stale_artifact_retry_agent.py");
  const counterFile = path.join(runtimeDir, "invocations.txt");
  const artifactDirsFile = path.join(runtimeDir, "artifact_dirs.txt");
  fs.writeFileSync(
    runtimeScript,
    [
      "import os, pathlib, sys",
      "prompt = sys.stdin.read()",
      "counter = pathlib.Path(__file__).with_name('invocations.txt')",
      "count = int(counter.read_text(encoding='utf-8') or '0') if counter.exists() else 0",
      "count += 1",
      "counter.write_text(str(count), encoding='utf-8')",
      "artifact_dir = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
      "artifact_dir.mkdir(parents=True, exist_ok=True)",
      `pathlib.Path(${JSON.stringify(artifactDirsFile)}).write_text((pathlib.Path(${JSON.stringify(artifactDirsFile)}).read_text(encoding='utf-8') if pathlib.Path(${JSON.stringify(artifactDirsFile)}).exists() else '') + str(count) + ':' + str(artifact_dir) + '\\n', encoding='utf-8')`,
      "if count == 1:",
      "    (artifact_dir / 'stale_failed.md').write_text('# 失败轮半截产物\\n\\nSTALE_FAILED_ARTIFACT: 这份旧文件不应该出现在重试后的下载里。\\n', encoding='utf-8')",
      "    print('正在分析源码，但本轮即将失败', flush=True)",
      "    raise SystemExit(9)",
      "(artifact_dir / 'final_complete.md').write_text('\\n'.join([",
      "    '# 完整测试设计',",
      "    '',",
      "    'NEW_COMPLETE_ARTIFACT: 重试后的完整产物。',",
      "    '',",
      "    '## 代码证据',",
      "    '- `lib/iscsi/iscsi.c`: `iscsi_login_retry_artifact_probe` 作为 iSCSI login 证据。',",
      "    '',",
      "    '## 流程梳理',",
      "    '1. Initiator 发起 Login Request。',",
      "    '2. Target 校验认证参数并返回 Login Response。',",
      "    '',",
      "    '## SFMEA',",
      "    '- failure mode: CHAP secret mismatch; cause: 错误 secret; effect: login failed; severity 7; occurrence 4; detection 3; RPN 84; mitigation: 观测 Login Response 与认证日志。',",
      "    '',",
      "    '## 黑盒测试用例',",
      "    '1. 用例：CHAP 失败后重试；前置条件：target 开启 CHAP；步骤：先用错误 secret 登录，再用正确 secret 重试；预期结果：首次失败可观测，第二次进入 Full Feature Phase；观测点：Login Response、认证日志和 session 状态。',",
      "]) + '\\n', encoding='utf-8')",
      "print('已生成文件：final_complete.md', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-stale-artifact-retry-e2e-${Date.now()}`;
  const runtimeName = `Stale artifact retry runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} retry artifact isolation`;
  const prompt = "分析 SPDK iSCSI login，输出代码证据、流程梳理、SFMEA 和黑盒测试用例，并把完整结果保存为文件";

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

    await expect(page.locator("div[role='alert']").filter({ hasText: "执行器退出码：9" })).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByRole("button", { name: "重试上一条" })).toBeVisible();
    await expect(page.getByText("STALE_FAILED_ARTIFACT")).toHaveCount(0);

    const firstRunResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}`,
    );
    expect(firstRunResp.ok()).toBeTruthy();
    const failedConversation = (await firstRunResp.json()) as {
      latest_run: { id: string; status: string } | null;
    };
    expect(failedConversation.latest_run?.status).toBe("failed");
    const failedArtifactDir = fs.readFileSync(artifactDirsFile, "utf8").trim().split("\n")[0].split(":").slice(1).join(":");
    expect(fs.existsSync(path.join(failedArtifactDir, "stale_failed.md"))).toBe(true);

    await page.getByRole("button", { name: "重试上一条" }).hover();
    await page.getByRole("button", { name: "重试上一条" }).click();
    const assistantArtifactMessage = page.locator(".ct-codex-message:not(.is-user)").filter({
      hasText: "已生成结构化产物",
    });
    await expect(assistantArtifactMessage).toBeVisible({
      timeout: 30_000,
    });
    await expect(assistantArtifactMessage).toContainText("完整内容已保存为下载产物");
    await expect(page.locator("div[role='alert']").filter({ hasText: "执行器退出码：9" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "下载完整产物" })).toBeVisible({ timeout: 15_000 });

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "下载完整产物" }).hover();
    await page.getByRole("link", { name: "下载完整产物" }).click();
    const download = await downloadPromise;
    const artifactPath = testInfo.outputPath("retry-latest-artifact.md");
    await download.saveAs(artifactPath);
    const artifact = fs.readFileSync(artifactPath, "utf8");
    expect(artifact).toContain("NEW_COMPLETE_ARTIFACT");
    expect(artifact).toContain("## 代码证据");
    expect(artifact).toContain("## 流程梳理");
    expect(artifact).toContain("## SFMEA");
    expect(artifact).toContain("## 黑盒测试用例");
    expect(artifact).not.toContain("STALE_FAILED_ARTIFACT");
    expect(artifact).not.toContain("正在分析源码，但本轮即将失败");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as {
      items: Array<{ role: string; content: string }>;
    };
    const assistant = [...messageBody.items].reverse().find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("下载完整产物");
    expect(assistant?.content).not.toContain("STALE_FAILED_ARTIFACT");
    expect(fs.readFileSync(counterFile, "utf8")).toBe("2");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
    await request.delete(`${backendBase}/api/workspaces/${encodeURIComponent(workspace.id)}`);
  }
});

test("materializes short source-evidence black-box answers as downloadable artifacts", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(70_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-short-blackbox-repo-")));
  fs.mkdirSync(path.join(repo, "lib", "iscsi"), { recursive: true });
  fs.mkdirSync(path.join(repo, "test", "iscsi_tgt"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "iscsi", "iscsi.c"),
    "int iscsi_op_login_update_param(void) { return 0; }\n",
    "utf8",
  );
  fs.writeFileSync(path.join(repo, "test", "iscsi_tgt", "login.sh"), "#!/bin/sh\n", "utf8");

  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-short-blackbox-runtime-")));
  const runtimeScript = path.join(runtimeDir, "short_blackbox_agent.py");
  fs.writeFileSync(
    runtimeScript,
    [
      "import sys",
      "sys.stdin.read()",
      "print('## 代码证据')",
      "print('- `lib/iscsi/iscsi.c:1539`: CHAP AuthMethod 协商路径。')",
      "print('- `test/iscsi_tgt`: 可承载登录黑盒回归。')",
      "print('')",
      "print('## 黑盒测试用例')",
      "print('### TC-01 正常登录')",
      "print('前置条件：target 已启动；步骤：initiator 发起 iSCSI Login；预期结果：进入 Full Feature Phase；观测点：Login Response、session 状态和日志。')",
      "print('')",
      "print('### TC-02 CHAP 失败')",
      "print('前置条件：target 开启 CHAP；步骤：使用错误 secret 登录；预期结果：Login Response 拒绝；观测点：认证失败日志和连接状态。')",
      "",
    ].join("\n"),
    "utf8",
  );

  const workspaceName = `ai-short-blackbox-e2e-${Date.now()}`;
  const runtimeName = `Short blackbox runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} source blackbox artifact`;
  const prompt = "针对 iSCSI 登录写两个黑盒用例，先读源码证据";

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
      timeout_seconds: 20,
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

    await expect(page.locator(".ct-codex-message:not(.is-user)").filter({ hasText: "已保存为下载产物" })).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByRole("link", { name: "下载完整产物" })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("AI 线程对话内容")).not.toContainText("Login Response 拒绝");

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "下载完整产物" }).hover();
    await page.getByRole("link", { name: "下载完整产物" }).click();
    const download = await downloadPromise;
    const artifactPath = testInfo.outputPath("short-source-blackbox-artifact.md");
    await download.saveAs(artifactPath);
    const artifact = fs.readFileSync(artifactPath, "utf8");
    expect(artifact).toContain("# " + threadTitle);
    expect(artifact).toContain("## 代码证据");
    expect(artifact).toContain("## 黑盒测试用例");
    expect(artifact).toContain("TC-02 CHAP 失败");
    expect(artifact).toContain("Login Response 拒绝");

    const messagesResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}/messages`,
    );
    expect(messagesResp.ok()).toBeTruthy();
    const messageBody = (await messagesResp.json()) as {
      items: Array<{ role: string; content: string }>;
    };
    const assistant = [...messageBody.items].reverse().find((item) => item.role === "assistant");
    expect(assistant?.content).toContain("下载完整产物");
    expect(assistant?.content).not.toContain("Login Response 拒绝");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
    await request.delete(`${backendBase}/api/workspaces/${encodeURIComponent(workspace.id)}`);
  }
});

test("keeps separate download links for multiple artifact turns in one AI thread", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(90_000);
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-multi-turn-artifacts-repo-")));
  fs.mkdirSync(path.join(repo, "lib", "bdev"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "lib", "bdev", "bdev.c"),
    "int bdev_multi_turn_artifact_probe(void) { return 0; }\n",
    "utf8",
  );
  const runtimeDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-agent-multi-turn-artifacts-")));
  const runtimeScript = path.join(runtimeDir, "multi_turn_artifact_agent.py");
  const counterFile = path.join(runtimeDir, "invocations.txt");
  fs.writeFileSync(
    runtimeScript,
    [
      "import os, pathlib, sys",
      "sys.stdin.read()",
      "counter = pathlib.Path(__file__).with_name('invocations.txt')",
      "count = int(counter.read_text(encoding='utf-8') or '0') if counter.exists() else 0",
      "count += 1",
      "counter.write_text(str(count), encoding='utf-8')",
      "artifact_dir = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
      "artifact_dir.mkdir(parents=True, exist_ok=True)",
      "marker = 'FIRST_TURN_ARTIFACT_MARKER' if count == 1 else 'SECOND_TURN_ARTIFACT_MARKER'",
      "title = '# 第一轮 bdev 测试设计' if count == 1 else '# 第二轮 bdev 测试设计'",
      "(artifact_dir / f'turn_{count}_report.md').write_text('\\n'.join([",
      "    title,",
      "    '',",
      "    marker + ': 当前下载必须只来自这一轮 run。',",
      "    '',",
      "    '## 代码证据',",
      "    '- `lib/bdev/bdev.c`: `bdev_multi_turn_artifact_probe` 约束 bdev 场景。',",
      "    '',",
      "    '## 流程梳理',",
      "    '1. 应用打开 bdev。',",
      "    '2. 提交 I/O 并等待 completion。',",
      "    '',",
      "    '## SFMEA',",
      "    '- failure mode: completion lost; cause: queue drain race; effect: I/O hang; severity 8; occurrence 3; detection 4; RPN 96; mitigation: 监控 I/O 状态和超时恢复。',",
      "    '',",
      "    '## 黑盒测试用例',",
      "    '1. 用例：bdev I/O 完成观测；前置条件：bdev 已创建；步骤：提交读写 I/O；预期结果：完成事件可观测且状态正确；观测点：RPC、日志、I/O 计数。',",
      "]) + '\\n', encoding='utf-8')",
      "print(f'已生成文件：turn_{count}_report.md', flush=True)",
      "",
    ].join("\n"),
    "utf8",
  );
  const workspaceName = `ai-multi-turn-artifacts-e2e-${Date.now()}`;
  const runtimeName = `Multi turn artifacts runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} artifact links`;

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
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("当前 AI 执行器")).toHaveValue(runtime.id);

    await page.getByLabel("AI 线程消息").fill("第一轮：生成 bdev I/O 完整测试设计、SFMEA 和黑盒用例文件");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByRole("link", { name: "下载完整产物" })).toBeVisible({ timeout: 30_000 });

    await page.getByLabel("AI 线程消息").fill("第二轮：生成 bdev reset/failover 完整测试设计、SFMEA 和黑盒用例文件");
    await page.getByRole("button", { name: "发送" }).hover();
    await page.getByRole("button", { name: "发送" }).click();
    const downloadLinks = page.getByRole("link", { name: "下载完整产物" });
    await expect(downloadLinks).toHaveCount(2, { timeout: 30_000 });

    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    const restoredLinks = page.getByRole("link", { name: "下载完整产物" });
    await expect(restoredLinks).toHaveCount(2, { timeout: 15_000 });
    const firstHref = await restoredLinks.nth(0).getAttribute("href");
    const secondHref = await restoredLinks.nth(1).getAttribute("href");
    expect(firstHref).toMatch(/\/api\/ai\/conversations\/[^/]+\/runs\/[^/]+\/artifact$/);
    expect(secondHref).toMatch(/\/api\/ai\/conversations\/[^/]+\/runs\/[^/]+\/artifact$/);
    expect(firstHref).not.toBe(secondHref);

    const firstDownloadPromise = page.waitForEvent("download");
    await restoredLinks.nth(0).hover();
    await restoredLinks.nth(0).click();
    const firstDownload = await firstDownloadPromise;
    const firstArtifactPath = testInfo.outputPath("multi-turn-first-artifact.md");
    await firstDownload.saveAs(firstArtifactPath);
    const firstArtifact = fs.readFileSync(firstArtifactPath, "utf8");
    expect(firstArtifact).toContain("FIRST_TURN_ARTIFACT_MARKER");
    expect(firstArtifact).not.toContain("SECOND_TURN_ARTIFACT_MARKER");

    const secondDownloadPromise = page.waitForEvent("download");
    await restoredLinks.nth(1).hover();
    await restoredLinks.nth(1).click();
    const secondDownload = await secondDownloadPromise;
    const secondArtifactPath = testInfo.outputPath("multi-turn-second-artifact.md");
    await secondDownload.saveAs(secondArtifactPath);
    const secondArtifact = fs.readFileSync(secondArtifactPath, "utf8");
    expect(secondArtifact).toContain("SECOND_TURN_ARTIFACT_MARKER");
    expect(secondArtifact).not.toContain("FIRST_TURN_ARTIFACT_MARKER");
    expect(fs.readFileSync(counterFile, "utf8")).toBe("2");
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(runtime.id)}`);
    await request.delete(`${backendBase}/api/workspaces/${encodeURIComponent(workspace.id)}`);
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

test("defaults new AI threads to a managed clowder-like executor even when custom runtimes exist", async ({
  page,
  request,
}) => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-ai-default-runtime-")));
  fs.writeFileSync(path.join(repo, "README.md"), "AI default runtime e2e workspace\n", "utf8");
  const workspaceName = `ai-default-runtime-${Date.now()}`;
  const runtimeName = `ZZZ custom runtime ${Date.now()}`;
  const threadTitle = `${workspaceName} default executor`;

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
  const customRuntime = (await runtimeResp.json()) as { id: string };

  const workspaceResp = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: workspaceName, repo_path: repo },
  });
  expect(workspaceResp.status()).toBe(201);
  const workspace = (await workspaceResp.json()) as { id: string };

  try {
    const enabledRuntimesResp = await request.get(`${backendBase}/api/settings/agent-runtimes?enabled=true`);
    expect(enabledRuntimesResp.ok()).toBeTruthy();
    const enabledRuntimes = (await enabledRuntimesResp.json()) as { items: Array<{ id: string; name: string }> };
    const expectedDefault =
      enabledRuntimes.items.find((item) => item.id === "default-claude-code") ??
      enabledRuntimes.items.find((item) => item.id === "default-codex") ??
      enabledRuntimes.items.find((item) => item.id === "default-opencode") ??
      enabledRuntimes.items[0] ??
      null;
    expect(expectedDefault).not.toBeNull();
    expect(expectedDefault?.id).not.toBe(customRuntime.id);

    await page.goto("/ai", { waitUntil: "domcontentloaded" });
    const projectButton = page.locator("button").filter({ hasText: workspaceName }).first();
    await expect(projectButton).toBeVisible({ timeout: 15_000 });
    await projectButton.hover();
    await projectButton.click();
    await expect(page.getByRole("heading", { name: workspaceName })).toBeVisible();

    const homeRuntimeSelect = page.getByLabel("AI 线程执行器");
    await expect(homeRuntimeSelect).toHaveValue(expectedDefault?.id ?? "builtin_llm");
    await expect(homeRuntimeSelect.locator(`option[value="${customRuntime.id}"]`)).toContainText(runtimeName);
    await page.getByPlaceholder(/线程名称/).fill(threadTitle);
    await page.getByRole("button", { name: "新建线程" }).hover();
    await page.getByRole("button", { name: "新建线程" }).click();

    await page.waitForURL(/\/ai\/[^/]+$/, { timeout: 15_000 });
    const threadId = page.url().split("/").pop() ?? "";
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({ timeout: 15_000 });
    const threadRuntimeSelect = page.getByLabel("当前 AI 执行器");
    await expect(threadRuntimeSelect).toHaveValue(expectedDefault?.id ?? "builtin_llm");
    await expect(page.locator(".ct-ai-env-card").filter({ hasText: "执行器" })).toContainText(
      expectedDefault?.name ?? "内置模型",
    );

    const conversationResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(threadId)}`,
    );
    expect(conversationResp.ok()).toBeTruthy();
    const conversation = (await conversationResp.json()) as {
      runtime_type: string;
      agent_runtime_id: string | null;
      workspace_id: string;
    };
    expect(conversation.runtime_type).toBe(expectedDefault ? "agent_runtime" : "builtin_llm");
    expect(conversation.agent_runtime_id).toBe(expectedDefault?.id ?? null);
    expect(conversation.agent_runtime_id).not.toBe(customRuntime.id);
    expect(conversation.workspace_id).toBe(workspace.id);
  } finally {
    await request.delete(`${backendBase}/api/settings/agent-runtimes/${encodeURIComponent(customRuntime.id)}`);
    await request.delete(`${backendBase}/api/workspaces/${encodeURIComponent(workspace.id)}`);
  }
});

test("recovers an AI thread to an enabled executor after its bound runtime is disabled", async ({
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
    const runtimeList = await request.get(`${backendBase}/api/settings/agent-runtimes?enabled=true`);
    expect(runtimeList.ok()).toBeTruthy();
    const enabledRuntimes = (await runtimeList.json()) as { items: Array<{ id: string; name: string }> };
    const expectedFallback =
      enabledRuntimes.items.find((item) => item.id === "default-claude-code") ??
      enabledRuntimes.items.find((item) => item.id === "default-codex") ??
      enabledRuntimes.items.find((item) => item.id === "default-opencode") ??
      enabledRuntimes.items[0] ??
      null;

    await page.goto(`/ai/${conversation.id}`, { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: threadTitle })).toBeVisible({
      timeout: 15_000,
    });
    const threadRuntimeSelect = page.getByLabel("当前 AI 执行器");
    await expect(page.locator(".ct-codex-ai__notice")).toContainText("已自动切换到", { timeout: 15_000 });
    await expect(threadRuntimeSelect).toHaveValue(expectedFallback?.id ?? "builtin_llm");
    await expect(threadRuntimeSelect.locator(`option[value="${runtime.id}"]`)).toHaveCount(0);
    await expect(page.locator(".ct-ai-env-card").filter({ hasText: "执行器" })).toContainText(
      expectedFallback?.name ?? "内置模型",
    );
    await expect(page.getByLabel("AI 线程消息")).toBeEnabled();
    await expect(page.getByRole("button", { name: "解释这个测试设计背后的风险判断" })).toBeEnabled();
    await page.getByLabel("AI 线程消息").fill("验证恢复后的执行器可以继续输入");
    await expect(page.getByRole("button", { name: "发送" })).toBeEnabled();

    const recoveredResp = await request.get(
      `${backendBase}/api/ai/conversations/${encodeURIComponent(conversation.id)}`,
    );
    expect(recoveredResp.ok()).toBeTruthy();
    const recovered = (await recoveredResp.json()) as {
      runtime_type: string;
      agent_runtime_id: string | null;
    };
    expect(recovered.runtime_type).toBe(expectedFallback ? "agent_runtime" : "builtin_llm");
    expect(recovered.agent_runtime_id).toBe(expectedFallback?.id ?? null);
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
