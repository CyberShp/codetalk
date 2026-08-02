import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const apiSource = readFileSync(
  join(root, "../src/lib/knowledge-center.ts"),
  "utf8",
);
const viewSource = readFileSync(
  join(root, "../src/app/workbench/knowledge-center-view.tsx"),
  "utf8",
);
const knowledgePageSource = readFileSync(
  join(root, "../src/app/knowledge-center/page.tsx"),
  "utf8",
);
const sidebarSource = readFileSync(
  join(root, "../src/components/layout/Sidebar.tsx"),
  "utf8",
);

test("knowledge center client exposes typed incident, pattern, import, and feedback actions", () => {
  for (const symbol of [
    "listKnowledgeIncidents",
    "getKnowledgeIncident",
    "listKnowledgePatterns",
    "addKnowledgePatternVersion",
    "restoreKnowledgePatternVersion",
    "reviewKnowledgePattern",
    "updateKnowledgePatternLifecycle",
    "listKnowledgeImportJobs",
    "retryKnowledgeImportStage",
    "importKnowledgePaste",
    "importKnowledgeFiles",
    "startKnowledgeAgentEnrichment",
    "recordKnowledgeFeedback",
  ]) {
    assert.match(apiSource, new RegExp(`\\b${symbol}\\b`));
  }
});

test("knowledge center view is a standalone three-tab management surface", () => {
  for (const label of ["历史事件", "经验模式", "导入任务", "Agent 提炼"]) {
    assert.match(viewSource, new RegExp(label));
  }
  assert.match(viewSource, /multiple/);
  assert.match(viewSource, /MR/);
  assert.match(viewSource, /provenance|溯源/);
  assert.match(viewSource, /restore|恢复/);
});

test("knowledge center keeps extraction honest and single-user", () => {
  assert.match(viewSource, /startKnowledgeAgentEnrichment/);
  assert.match(viewSource, /导入材料/);
  assert.doesNotMatch(viewSource, /团队|成员|角色|审批|owner|team/i);
});

test("Phase2 exposes knowledge as a standalone management surface", () => {
  assert.match(knowledgePageSource, /KnowledgeCenterView/);
  assert.match(sidebarSource, /href:\s*["']\/knowledge-center["']/);
  assert.match(sidebarSource, /href:\s*["']\/artifact-profiles["']/);
});
