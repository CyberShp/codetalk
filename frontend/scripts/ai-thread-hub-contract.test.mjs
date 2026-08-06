import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../src/app/ai/page.tsx", import.meta.url), "utf8");
const threadSource = readFileSync(new URL("../src/app/ai/[id]/page.tsx", import.meta.url), "utf8");
const globalCss = readFileSync(new URL("../src/app/globals.css", import.meta.url), "utf8");

test("AI thread hub exposes deletion through the real conversation API", () => {
  assert.match(source, /api\.aiConversations\.delete/);
  assert.match(source, /aria-label=\{`删除线程 \$\{thread\.title\}`\}/);
  assert.match(source, /window\.confirm\(`删除线程/);
});

test("AI thread no longer creates Workflow-bound task drafts", () => {
  assert.doesNotMatch(source, /api\.workbench\.workflows\.list/);
  assert.doesNotMatch(source, /aria-label="线程工作流模板"/);
  assert.match(threadSource, /历史线程绑定了/);
  assert.match(threadSource, /进入 Skill 任务向导/);
  const removedCreateTaskDraftPattern = new RegExp(String.raw`api\.aiConversations\.create` + String.raw`TaskDraft`);
  assert.doesNotMatch(threadSource, removedCreateTaskDraftPattern);
  assert.doesNotMatch(threadSource, /\/tasks\/new\?task=/);
});

test("AI thread composer preserves multiline prompts until explicit send", () => {
  assert.match(threadSource, /event\.key === "Enter" && \(event\.metaKey \|\| event\.ctrlKey\)/);
  assert.doesNotMatch(threadSource, /event\.key === "Enter" && !event\.shiftKey/);
});

test("AI thread translates persisted internal parser errors before display and export", () => {
  assert.match(threadSource, /function publicAgentErrorText/);
  assert.match(
    threadSource,
    /cleaned\.startsWith\("测试活动产物未通过质量门禁"\)/,
    "AI thread should show CodeTalk quality-gate failures instead of replacing them with a generic executor error",
  );
  assert.match(
    threadSource,
    /cleaned\.startsWith\("模型输出达到长度上限"\)/,
    "AI thread should show the actionable truncation reason",
  );
  assert.match(
    threadSource,
    /cleaned\.startsWith\("绑定工作流交付件未通过验收"\)/,
    "AI thread should show the exact missing workflow artifact and repair action",
  );
  assert.match(threadSource, /separator is not found/i);
  assert.match(threadSource, /publicAgentErrorText\(latestRun\.error\)/);
  assert.match(threadSource, /publicAgentErrorText\(conversation\.latest_run\.error\)/);
  assert.match(threadSource, /执行器启动失败。请检查设置中的命令、工作目录和执行权限后重试。/);
  assert.match(threadSource, /执行器运行失败。请展开 Agent 过程查看内部诊断，然后重试或切换执行器。/);
  assert.match(threadSource, /\^执行器超时（\\d\+s）\$/);
});

test("AI thread keeps line-numbered source output out of the latest process summary", () => {
  assert.match(threadSource, /function looksLikeReadableNumericProgress/);
  const looksLikeReadableNumericProgress = (value) =>
    /^\d{1,7}\s+(?:(?:tests?|files?|items?|steps?|nodes?|cases?|warnings?|errors?|percent)\b|%)/i.test(value);
  assert.equal(looksLikeReadableNumericProgress("3 tests passed"), true);
  assert.equal(looksLikeReadableNumericProgress("12 files changed"), true);
  assert.equal(looksLikeReadableNumericProgress("100 percent complete"), true);
  assert.equal(looksLikeReadableNumericProgress("94 iscsitestfini"), false);
  assert.equal(looksLikeReadableNumericProgress("12 files_changed++;"), false);
  assert.equal(looksLikeReadableNumericProgress("7 item_count = 3;"), false);
});

test("AI thread lifecycle gives the final run state authority over stale process events", () => {
  const start = threadSource.indexOf("function agentLifecycleLabel");
  const end = threadSource.indexOf("function agentRunElapsedLabel", start);
  const lifecycleSource = threadSource.slice(start, end);
  assert.ok(start >= 0 && end > start);
  assert.ok(
    lifecycleSource.indexOf('runStatus === "completed"') <
      lifecycleSource.indexOf('latestKind === "artifact"'),
    "completed must win over a stale running/artifact process event",
  );
  assert.ok(
    lifecycleSource.indexOf('runStatus === "completed"') <
      lifecycleSource.indexOf('latestKind === "error"'),
    "completed must win over a stale error process event after recovery",
  );
});

test("AI thread layout keeps mobile reading usable and wraps long evidence text", () => {
  assert.match(
    threadSource,
    /<div className="ct-codex-ai__chrome">\s*<header className="ct-codex-ai__topbar">/,
    "top bar and actionable errors must share one auto-sized grid row above the scroll reader",
  );
  assert.match(
    globalCss,
    /\.ct-codex-ai__chrome\s*\{[\s\S]*?position:\s*relative;[\s\S]*?z-index:\s*2;/,
    "actionable errors must remain above long message content and accept pointer events",
  );
  assert.match(
    globalCss,
    /\.ct-page-shell:has\(\.ct-codex-ai\)\s*\{[\s\S]*?height:\s*calc\(100dvh - var\(--ct-mobile-nav-height\)\);[\s\S]*?overflow:\s*hidden;/,
  );
  assert.match(
    globalCss,
    /\.ct-codex-ai\s*\{[\s\S]*?height:\s*calc\(100vh - 48px\);[\s\S]*?overflow:\s*hidden;/,
  );
  assert.match(
    globalCss,
    /\.ct-codex-ai,\s*\n\s*\.ct-codex-ai\.is-context-open\s*\{[\s\S]*?height:\s*100%;[\s\S]*?min-height:\s*0;[\s\S]*?overflow:\s*hidden;/,
  );
  assert.match(globalCss, /\.ct-codex-ai__reader\s*\{[\s\S]*?min-height:\s*0;[\s\S]*?overflow:\s*auto;/);
  assert.match(globalCss, /\.ct-ai-ref\s*\{[\s\S]*?overflow-wrap:\s*anywhere;/);
  assert.match(globalCss, /\.ct-ai-ref__meta code\s*\{[\s\S]*?white-space:\s*normal;/);
  assert.match(globalCss, /\.ct-agent-process summary strong\s*\{[\s\S]*?white-space:\s*normal;/);
});
