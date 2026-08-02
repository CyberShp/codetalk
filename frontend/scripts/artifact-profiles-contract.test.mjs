import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const apiSource = readFileSync(
  join(root, "../src/lib/artifact-profiles.ts"),
  "utf8",
);
const viewSource = readFileSync(
  join(root, "../src/app/workbench/artifact-profiles-view.tsx"),
  "utf8",
);
const taskApiSource = readFileSync(
  join(root, "../src/lib/api/workbench-tasks.ts"),
  "utf8",
);
const taskDetailSource = readFileSync(
  join(root, "../src/features/tasks/workbench-task-detail-page.tsx"),
  "utf8",
);

test("artifact profiles expose local CRUD, versions, restore, bindings, and resolution", () => {
  assert.match(apiSource, /listArtifactProfiles/);
  assert.match(apiSource, /createArtifactProfile/);
  assert.match(apiSource, /updateArtifactProfile/);
  assert.match(apiSource, /restoreArtifactProfileVersion/);
  assert.match(apiSource, /setDefaultArtifactProfile/);
  assert.match(apiSource, /bindWorkspaceArtifactProfile/);
  assert.match(apiSource, /resolveArtifactProfile/);
});

test("profile editor uses form controls and explains immutable safety", () => {
  assert.match(viewSource, /交付件档案/);
  assert.match(viewSource, /产物 ID/);
  assert.match(viewSource, /文件名/);
  assert.match(viewSource, /必需/);
  assert.match(viewSource, /证据校验、路径校验和清单生成不能被档案关闭/);
  assert.match(viewSource, /恢复此版本/);
  assert.match(viewSource, /bindWorkspaceArtifactProfile/);
  assert.match(viewSource, /bindFeatureArtifactProfile/);
  assert.match(viewSource, /api\.workspaces\.list\(\)/);
  assert.match(viewSource, /工作空间绑定/);
  assert.match(viewSource, /特性标签绑定/);
});

test("profile editor keeps the product single-user", () => {
  assert.doesNotMatch(viewSource, /团队|成员|角色|审批|owner|team/i);
});

test("run preparation can freeze one explicitly selected artifact profile", () => {
  assert.match(taskApiSource, /artifact_profile_id:\s*artifactProfileId/);
  assert.match(taskDetailSource, /交付件档案/);
  assert.match(taskDetailSource, /artifactProfileId/);
});
