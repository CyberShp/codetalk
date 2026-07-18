import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const inspector = readFileSync(
  new URL("../src/features/workflows/designer/node-inspector.tsx", import.meta.url),
  "utf8",
);
const wizard = readFileSync(
  new URL("../src/features/tasks/task-wizard.tsx", import.meta.url),
  "utf8",
);
const cockpit = readFileSync(
  new URL("../src/features/runs/run-cockpit-page.tsx", import.meta.url),
  "utf8",
);
const styles = readFileSync(
  new URL("../src/app/globals.css", import.meta.url),
  "utf8",
);

test("designer exposes the governed mindmap output without manual JSON", () => {
  assert.match(inspector, /测试设计脑图/);
  assert.match(inspector, /test_design_mindmap/);
  assert.match(inspector, /test_design_mindmap\.json/);
  assert.match(inspector, /test_design_mindmap\.html/);
  assert.match(inspector, /test_design_mindmap\.svg/);
  assert.match(inspector, /companion_artifacts/);
});

test("task wizard keeps optional mindmap disabled by default and offers a Chinese choice", () => {
  assert.match(wizard, /default_enabled/);
  assert.match(wizard, /测试设计脑图/);
  assert.match(wizard, /value="test_design_mindmap"/);
  assert.match(wizard, /test_design_mindmap\.json/);
  assert.match(wizard, /function outputEnabled/);
  assert.match(wizard, /手动填写/);
  assert.doesNotMatch(wizard, /\}\s*·\s*manual/);
});

test("cockpit renders a bounded interactive mindmap instead of raw JSON", () => {
  assert.match(cockpit, /function TestDesignMindmapPreview/);
  assert.match(cockpit, /搜索脑图节点/);
  assert.match(cockpit, /全部优先级/);
  assert.match(cockpit, /全部节点类型/);
  assert.match(cockpit, /全部状态/);
  assert.match(cockpit, /折叠到两层/);
  assert.match(cockpit, /artifactContent\(runId, path, 2_000_000\)/);
  assert.match(styles, /\.ct-v2-mindmap-preview[\s\S]{0,240}max-height:/);
  assert.match(styles, /\.ct-v2-mindmap-tree[\s\S]{0,180}overflow:\s*auto/);
});

test("cockpit keeps total runtime live and presents workflow nodes in Chinese", () => {
  assert.match(cockpit, /useRunClock/);
  assert.match(cockpit, /function RunDuration/);
  assert.doesNotMatch(cockpit, /const runClockMs = useRunClock/);
  assert.match(cockpit, /function displayNodeName/);
  assert.match(cockpit, /const partial = runPartial \|\|/);
  assert.match(cockpit, /工作流已结束，当前最佳结果已保留/);
  assert.match(cockpit, /运行已结束/);
  assert.match(cockpit, /节点因上游门禁阻断/);
  assert.doesNotMatch(cockpit, /<strong>\{currentNode\?\.label \|\| "等待调度"\}<\/strong>/);
});
