import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const api = readFileSync(new URL("../src/lib/api/workflows.ts", import.meta.url), "utf8");
const entry = readFileSync(
  new URL("../src/features/workflows/canvas-entry.tsx", import.meta.url),
  "utf8",
);
const types = readFileSync(
  new URL("../src/lib/types/workflow.ts", import.meta.url),
  "utf8",
);
const library = readFileSync(
  new URL("../src/features/workflows/workflow-library-page.tsx", import.meta.url),
  "utf8",
);
const taskWizard = readFileSync(
  new URL("../src/features/tasks/task-wizard.tsx", import.meta.url),
  "utf8",
);
const versionDetail = readFileSync(
  new URL("../src/features/workflows/workflow-version-detail-page.tsx", import.meta.url),
  "utf8",
);
const designer = readFileSync(
  new URL("../src/features/workflows/designer/workflow-designer.tsx", import.meta.url),
  "utf8",
);
const workflowWizard = readFileSync(
  new URL("../src/features/workflows/workflow-wizard/workflow-wizard.tsx", import.meta.url),
  "utf8",
);
const cockpit = readFileSync(
  new URL("../src/features/runs/run-cockpit-page.tsx", import.meta.url),
  "utf8",
);
const versionsPage = readFileSync(
  new URL("../src/features/workflows/workflow-versions-page.tsx", import.meta.url),
  "utf8",
);
const taskCenter = readFileSync(
  new URL("../src/features/tasks/task-center-page.tsx", import.meta.url),
  "utf8",
);
const taskDetail = readFileSync(
  new URL("../src/features/tasks/workbench-task-detail-page.tsx", import.meta.url),
  "utf8",
);
const legacyRunView = readFileSync(
  new URL("../src/app/workbench/run-view.tsx", import.meta.url),
  "utf8",
);

test("Phase 7 workflow chooser is driven by the versioned backend catalog", () => {
  assert.match(api, /workflow-templates/);
  assert.match(api, /listTemplates/);
  assert.match(types, /migration_contract_version: number/);
  assert.match(entry, /workflowsApi\.listTemplates\(\)/);
  assert.match(entry, /workflow-template-\$\{item\.id\}/);
  assert.match(entry, /模板目录加载失败/);
  assert.match(entry, /模板版本不兼容/);
  assert.match(entry, />重试</);
});

test("Phase 7 template IDs are not duplicated as hard-coded chooser cards", () => {
  assert.doesNotMatch(entry, /<strong>自由源码分析<\/strong>/);
  assert.doesNotMatch(entry, /<strong>空白画布<\/strong>/);
  assert.match(entry, /item\.presentation\.scope === "professional"/);
});

test("Legacy presentation metadata is visible without exposing internal IDs", () => {
  assert.match(types, /presentation\?: WorkflowPresentation/);
  assert.match(library, /item\.presentation\?\.label/);
  assert.match(library, /Legacy/);
  assert.doesNotMatch(library, /搜索名称、ID 或描述/);
  assert.doesNotMatch(library, /<small>\{item\.id\}<\/small>/);
  assert.doesNotMatch(taskWizard, /published_version_id\?\.slice/);
  assert.match(taskWizard, /已发布版本 V/);
});

test("Historical versions preview migration before creating a V3 copy", () => {
  assert.match(api, /previewVersionMigration/);
  assert.match(versionDetail, /预览并复制为 V3/);
  assert.match(versionDetail, /enabled_professional_rules/);
  assert.match(versionDetail, /rollback_effect/);
  assert.match(versionDetail, /确认创建 V3 副本/);
  assert.doesNotMatch(versionDetail, /<strong>\{nodeId\}<\/strong>/);
  assert.doesNotMatch(versionDetail, /graph\?\.name \?\? workflowId/);
});

test("Copy to V3 requires an explicit confirmation for the displayed preview", () => {
  assert.match(types, /export interface WorkflowMigrationConfirmation/);
  assert.match(types, /confirmation_token: string/);
  assert.match(api, /copyVersionToV3: \(workflowId: string, versionId: string, confirmation: WorkflowMigrationConfirmation\)/);
  assert.match(api, /body: JSON\.stringify\(confirmation\)/);
  assert.doesNotMatch(api, /copyVersionToV3[\s\S]*body: "\{\}"/);
  assert.match(versionDetail, /preview\.confirmation_token/);
  assert.match(versionDetail, /preview_confirmed: true/);
  assert.match(versionDetail, /migration_contract_version: preview\.migration_contract_version/);
  assert.doesNotMatch(designer, /copyVersionToV3/);
  assert.doesNotMatch(workflowWizard, /copyVersionToV3/);
  assert.match(designer, /查看 V3 迁移预览/);
  assert.match(workflowWizard, /查看 V3 迁移预览/);
});

test("Published V1 and V2 entry points route to migration preview before creating drafts", () => {
  const entryPoints = [
    ["library", library],
    ["versions page", versionsPage],
    ["designer", designer],
  ];

  for (const [name, source] of entryPoints) {
    assert.match(source, /function requiresMigrationPreview\(version: WorkflowVersion\)/, `${name} must classify published historical versions`);
    assert.match(source, /editor_mode === "read_only_legacy"/, `${name} must classify read-only legacy versions`);
    assert.match(source, /editor_mode === "legacy"/, `${name} must classify legacy editor versions`);
    assert.match(source, /schema_version === 1/, `${name} must classify V1 schemas`);
    assert.match(source, /schema_version === 2/, `${name} must classify V2 schemas`);
    const historicalBranch = source.match(
      /if \(requiresMigrationPreview\(published\)\) \{([\s\S]*?return;)\s*\}/,
    );
    assert.ok(historicalBranch, `${name} must route historical published versions to the confirmation preview`);
    assert.match(historicalBranch[1], /\/versions\//, `${name} must open the version preview`);
    assert.doesNotMatch(
      historicalBranch[1],
      /workflowsApi\.createDraft/,
      `${name} must not create a draft from a historical published version`,
    );
  }
});

test("The retired unconfirmed copy API helper is absent", () => {
  assert.doesNotMatch(api, /copyAsCustomDraft/);
});

test("Unknown frozen contract versions get an actionable compatibility notice", () => {
  assert.match(cockpit, /不支持的冻结契约版本/);
  assert.match(cockpit, /历史运行仍可查看和下载/);
  assert.match(cockpit, /复制为受支持的 V3 工作流/);
  assert.match(cockpit, /axes\.unsupportedVersion/);
});

test("Normal history and task views use friendly versions instead of internal IDs", () => {
  assert.doesNotMatch(versionsPage, /<small>\{version\.version_id\}<\/small>/);
  assert.doesNotMatch(taskCenter, /workflow_version_id\.slice/);
  assert.doesNotMatch(taskCenter, /\{run\.workflow_id\}/);
  assert.doesNotMatch(taskDetail, /detail=\{task\.workflow_version_id\}/);
  assert.match(taskCenter, /workflow_version_number/);
  assert.match(taskDetail, /workflow_version_number/);
});

test("V3 text inputs preserve multiline user text in the task wizard", () => {
  assert.match(taskWizard, /\["text", "long_text"\]\.includes\(type\) \? <textarea/);
});

test("V3 attempts expose only the scheduler-owned workflow execution action", () => {
  assert.match(legacyRunView, /const isV3PreparedRun =/);
  assert.match(legacyRunView, /workflow_snapshot\.compiled_contract_version === 3/);
  assert.equal(
    [...legacyRunView.matchAll(/!isV3PreparedRun && \(/g)].length,
    3,
  );
  assert.match(legacyRunView, /executePreparedAgentRun\(stepId\)/);
});

test("Designer exposes formal release profile and profile-generated execution plan", () => {
  assert.match(designer, /<option value="formal_release">正式发布<\/option>/);
  assert.match(designer, /function ProfileExecutionPreview/);
  assert.match(designer, /workflow-profile-execution-preview/);
  assert.match(designer, /independent_review: "独立 Reviewer"/);
  assert.match(designer, /human_approval: "人工审批"/);
  assert.match(designer, /发布前请确认 Profile 生成的 Validator、Reviewer 和人工审批/);
  assert.match(designer, /profileGeneratedPlanNodes\(compiled\.compiled_plan\)\.length > 0/);
  assert.match(designer, /setBottomTab\("plan"\)/);
});
