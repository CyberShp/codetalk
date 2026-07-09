import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(
  join(root, "../src/app/workbench/agent-workbench-experience.tsx"),
  "utf8",
);

test("workbench cockpit treats weak-success states as review, not normal running", () => {
  assert.match(source, /completed_empty/);
  assert.match(source, /needs_review/);
  assert.match(source, /完成但信息不足/);
  assert.match(source, /需要复核/);
  assert.match(source, /runPanelStatus[\s\S]*验收提醒/);
});

test("workbench cockpit renders restart and review task-run events in Chinese", () => {
  assert.match(source, /interrupted:\s*["']运行中断["']/);
  assert.match(source, /needs_review:\s*["']需要复核["']/);
  assert.match(source, /completed_empty:\s*["']完成但信息不足["']/);
});

