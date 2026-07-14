import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const cockpit = readFileSync(
  new URL("../src/features/runs/run-cockpit-page.tsx", import.meta.url),
  "utf8",
);
const tasks = readFileSync(
  new URL("../src/features/tasks/task-center-page.tsx", import.meta.url),
  "utf8",
);
const semantic = readFileSync(
  new URL("../src/features/semantic-library/semantic-library-page.tsx", import.meta.url),
  "utf8",
);
const evidence = readFileSync(
  new URL("../src/features/evidence-library/evidence-library-page.tsx", import.meta.url),
  "utf8",
);
const api = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const taskWizard = readFileSync(
  new URL("../src/features/tasks/task-wizard.tsx", import.meta.url),
  "utf8",
);
const routeGate = readFileSync(
  new URL("../src/features/release/workbench-v2-route-gate.tsx", import.meta.url),
  "utf8",
);

test("run cockpit keeps a 2,000 event window and pages older events", () => {
  assert.match(cockpit, /MAX_LOADED_EVENTS\s*=\s*2000/);
  assert.match(cockpit, /tail:\s*true/);
  assert.match(cockpit, /before_id/);
  assert.match(cockpit, /加载更早事件/);
});

test("release searches debounce for 300ms", () => {
  assert.match(tasks, /SEARCH_DEBOUNCE_MS\s*=\s*300/);
  assert.match(semantic, /setTimeout\([^,]+,\s*300\)/);
  assert.match(evidence, /setTimeout\([^,]+,\s*300\)/);
});

test("task and asset libraries expose 25-row server pagination", () => {
  for (const source of [tasks, semantic, evidence]) {
    assert.match(source, /PAGE_SIZE\s*=\s*25/);
    assert.match(source, /上一页/);
    assert.match(source, /下一页/);
  }
});

test("isolated backend 3124 is only a fallback for frontend 3123", () => {
  assert.match(api, /if \(port === "3123"\) return \[sameHost\("3124"\)\]/);
  assert.doesNotMatch(api, /const candidates = \[sameHost\("3004"\), sameHost\("3124"\)\]/);
  assert.match(api, /if \(init\?\.body && !headers\.has\("Content-Type"\)\)/);
});

test("all direct V2 route families honor the backend rollback switch", () => {
  assert.match(routeGate, /workbenchReleaseApi\.get\(\)/);
  assert.match(routeGate, /router\.replace\(legacyDestination\)/);
  for (const relative of [
    "../src/app/tasks/layout.tsx",
    "../src/app/workflows/layout.tsx",
    "../src/app/semantic-library/layout.tsx",
    "../src/app/evidence-library/layout.tsx",
  ]) {
    const source = readFileSync(new URL(relative, import.meta.url), "utf8");
    assert.match(source, /WorkbenchV2RouteGate/);
  }
});

test("cockpit reconnects after transient SSE errors and pause freezes visible history", () => {
  assert.doesNotMatch(cockpit, /stream\.onerror\s*=\s*\(\)\s*=>\s*stream\.close\(\)/);
  assert.match(cockpit, /stream\.onerror\s*=.*refresh\(true\)/s);
  assert.match(cockpit, /pauseBoundary/);
  assert.match(cockpit, /frozenEvents/);
  assert.match(cockpit, /paused\s*\?\s*frozenEvents\s*:\s*events/);
  assert.match(cockpit, /setEvents\(\(current\)\s*=>\s*mergeEvents\(current,\s*eventResult\.items\)\)/);
  assert.doesNotMatch(cockpit, /if \(paused\) return \[\]/);
});

test("task wizard only offers Agent resource overrides for Agent nodes", () => {
  assert.match(taskWizard, /steps\.filter\(\(item\) => item\.type === "agent_task"\)/);
});

test("task wizard offers every published workflow, including migrated built-ins", () => {
  assert.match(taskWizard, /Boolean\(item\.v2\?\.published_version_id\)/);
  assert.doesNotMatch(taskWizard, /item\.authoring_graph\?\.schema_version === 2/);
});

test("task wizard treats the reserved repo_path directory as workspace-managed", () => {
  assert.match(taskWizard, /function isWorkspaceInputDefinition/);
  assert.match(taskWizard, /String\(item\.id\) === "repo_path"/);
  assert.match(taskWizard, /String\(item\.type\) === "directory"/);
});
