import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../src/app/ai/page.tsx", import.meta.url), "utf8");

test("AI thread hub exposes deletion through the real conversation API", () => {
  assert.match(source, /api\.aiConversations\.delete/);
  assert.match(source, /aria-label=\{`删除线程 \$\{thread\.title\}`\}/);
  assert.match(source, /window\.confirm\(`删除线程/);
});
