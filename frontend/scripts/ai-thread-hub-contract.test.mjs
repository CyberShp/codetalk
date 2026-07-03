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

test("AI thread composer preserves multiline prompts until explicit send", () => {
  assert.match(threadSource, /event\.key === "Enter" && \(event\.metaKey \|\| event\.ctrlKey\)/);
  assert.doesNotMatch(threadSource, /event\.key === "Enter" && !event\.shiftKey/);
});

test("AI thread layout keeps mobile reading usable and wraps long evidence text", () => {
  assert.match(globalCss, /\.ct-codex-ai,\s*\n\s*\.ct-codex-ai\.is-context-open\s*\{[\s\S]*?height:\s*max\(660px,\s*calc\(100vh - 190px\)\);/);
  assert.match(globalCss, /\.ct-codex-ai__reader\s*\{[\s\S]*?min-height:\s*300px;/);
  assert.match(globalCss, /\.ct-ai-ref\s*\{[\s\S]*?overflow-wrap:\s*anywhere;/);
  assert.match(globalCss, /\.ct-ai-ref__meta code\s*\{[\s\S]*?white-space:\s*normal;/);
  assert.match(globalCss, /\.ct-agent-process summary strong\s*\{[\s\S]*?white-space:\s*normal;/);
});
