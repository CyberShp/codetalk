import { createHash } from "node:crypto";
import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

const frontendPort = process.env.CODETALK_FRONTEND_PORT ?? "3003";
const backendPort = process.env.CODETALK_BACKEND_PORT ?? "3004";
const backendBase = `http://localhost:${backendPort}`;
const evidenceDir = process.env.CODETALK_E2E_ARTIFACT_DIR ?? "/Volumes/Media/codetalk-e2e-artifacts/phase7/history-artifacts-unspecified";
const taskId = "task-historical";
const taskRunId = "task_run_phase0_historical";
const largeArtifactPath = "support/historical-results.json";

assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "Phase 7 frozen history and artifact browser acceptance",
});

type HistoricalWorkflowFixture = {
  workflow_header: { workflow_id: string; published_version_id: string };
  workflow_version: { authoring_graph: { schema_version: number } };
};

type SeedResult = {
  fixtures: HistoricalWorkflowFixture[];
  hashes: Record<string, string>;
};

function assertPhase7IsolatedRuntime() {
  if (frontendPort !== "3233" || backendPort !== "3234") {
    throw new Error(
      "Phase 7 history acceptance must run on isolated ports 3233/3234. " +
        "Set CODETALK_FRONTEND_PORT=3233 CODETALK_BACKEND_PORT=3234 CODETALK_REUSE_EXISTING_SERVER=0.",
    );
  }
}

function evidencePath(...parts: string[]) {
  const target = path.join(evidenceDir, ...parts);
  mkdirSync(path.dirname(target), { recursive: true });
  return target;
}

function sha256(data: Buffer | string) {
  return createHash("sha256").update(data).digest("hex");
}

function seedFrozenHistory(): SeedResult {
  const dataDir = process.env.CODETALK_PLAYWRIGHT_DATA_DIR;
  expect(dataDir, "isolated Playwright data directory must be available").toBeTruthy();
  const fixturesDir = path.join(process.cwd(), "../backend/tests/fixtures/harness_workflow_refactor");
  const seed = spawnSync(
    process.env.CODETALK_BACKEND_PYTHON ?? "python3.11",
    [
      "-c",
      [
        "import hashlib, json, sqlite3, sys",
        "from pathlib import Path",
        "from app.services.workflow_version_store import WorkflowVersionStore",
        "from app.services.workbench_task_store import WorkbenchTaskStore",
        "from app.services.workbench_artifact_manifest import write_task_artifact_manifest",
        "data_dir = Path(sys.argv[1])",
        "fixtures_dir = Path(sys.argv[2])",
        "workbench = data_dir / 'workbench'",
        "workbench.mkdir(parents=True, exist_ok=True)",
        "workflow_db = workbench / 'workflows.db'",
        "WorkflowVersionStore(workflow_db).initialize_and_migrate()",
        "fixtures = []",
        "for name in ('v1-published-workflow.json', 'v2-published-workflow.json'):",
        " fixture = json.loads((fixtures_dir / name).read_text(encoding='utf-8'))",
        " fixtures.append(fixture)",
        " header, version = fixture['workflow_header'], fixture['workflow_version']",
        " serialized = [json.dumps(version[field], ensure_ascii=False, sort_keys=True) for field in ('authoring_graph', 'compiled_definition', 'compiled_plan', 'validation')]",
        " with sqlite3.connect(workflow_db) as db:",
        "  db.execute('INSERT INTO workflow_headers(workflow_id, name, description, status, published_version_id, current_draft_version_id, created_at, updated_at, archived_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', tuple(header[field] for field in ('workflow_id', 'name', 'description', 'status', 'published_version_id', 'current_draft_version_id', 'created_at', 'updated_at', 'archived_at')))",
        "  db.execute('INSERT INTO workflow_versions(version_id, workflow_id, version_number, state, authoring_graph_json, compiled_definition_json, compiled_plan_json, validation_json, based_on_version_id, created_at, updated_at, published_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (version['version_id'], version['workflow_id'], version['version_number'], version['state'], *serialized, version['based_on_version_id'], version['created_at'], version['updated_at'], version['published_at']))",
        "attempt = json.loads((fixtures_dir / 'historical-task-attempt.json').read_text(encoding='utf-8'))",
        "task = attempt['task']",
        "task_db = workflow_db",
        "WorkbenchTaskStore(task_db).initialize_and_migrate()",
        "with sqlite3.connect(task_db) as db:",
        " db.execute('INSERT INTO workbench_tasks(task_id, name, description, workspace_id, workflow_id, workflow_version_id, lifecycle_status, execution_profile_id, input_values_json, execution_overrides_json, output_overrides_json, tags_json, last_run_id, created_at, updated_at, archived_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (task['task_id'], task['name'], task['description'], task['workspace_id'], task['workflow_id'], task['workflow_version_id'], task['lifecycle_status'], task['execution_profile_id'], json.dumps(task['input_values'], ensure_ascii=False, sort_keys=True), json.dumps(task['execution_overrides'], ensure_ascii=False, sort_keys=True), json.dumps(task['output_overrides'], ensure_ascii=False, sort_keys=True), json.dumps(task['tags'], ensure_ascii=False), task['last_run_id'], task['created_at'], task['updated_at'], task['archived_at']))",
        "task_run_dir = workbench / 'task_runs' / attempt['task_run']['task_run_id']",
        "task_run_dir.mkdir(parents=True, exist_ok=True)",
        "task_run = dict(attempt['task_run'])",
        "task_run['artifact_dir'] = str(task_run_dir)",
        "(task_run_dir / 'task_run.json').write_text(json.dumps(task_run, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')",
        "(task_run_dir / 'task_run_events.jsonl').write_text('\\n'.join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in attempt['events']) + '\\n', encoding='utf-8')",
        "snapshot = json.loads((fixtures_dir / 'historical-run-snapshot-v3.json').read_text(encoding='utf-8'))",
        "(task_run_dir / 'run_snapshot_v3.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')",
        "artifacts = json.loads((fixtures_dir / 'historical-artifacts.json').read_text(encoding='utf-8'))",
        "for filename, payload in artifacts['components'].items():",
        " (task_run_dir / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')",
        "for filename, content in artifacts['deliverables'].items():",
        " (task_run_dir / filename).write_text(content, encoding='utf-8')",
        "large = {'schema_version': 1, 'entries': [{'id': index, 'summary': f'historical result {index:03d} ' + ('frozen evidence payload ' * 32), 'frozen': True} for index in range(1, 102)]}",
        "support = task_run_dir / 'support'",
        "support.mkdir(exist_ok=True)",
        "(support / 'historical-results.json').write_text(json.dumps(large, ensure_ascii=False, indent=2, sort_keys=True) + '\\n', encoding='utf-8')",
        "write_task_artifact_manifest(task_run_dir, task_run_id=task_run['task_run_id'])",
        "tracked = ['task_run.json', 'task_run_events.jsonl', 'run_snapshot_v3.json', 'report.md', 'support/historical-results.json']",
        "for fixture in fixtures:",
        " tracked.append(f\"workflow:{fixture['workflow_header']['workflow_id']}:{fixture['workflow_header']['published_version_id']}\")",
        "hashes = {}",
        "for item in tracked:",
        " if item.startswith('workflow:'):",
        "  _, workflow_id, version_id = item.split(':', 2)",
        "  with sqlite3.connect(workflow_db) as db:",
        "   row = db.execute('SELECT workflow_id, version_id, version_number, state, authoring_graph_json, compiled_definition_json, compiled_plan_json, validation_json, based_on_version_id, created_at, updated_at, published_at FROM workflow_versions WHERE workflow_id = ? AND version_id = ?', (workflow_id, version_id)).fetchone()",
        "  hashes[item] = hashlib.sha256(json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()",
        " else:",
        "  hashes[item] = hashlib.sha256((task_run_dir / item).read_bytes()).hexdigest()",
        "print(json.dumps({'fixtures': fixtures, 'hashes': hashes}, ensure_ascii=False))",
      ].join("\n"),
      dataDir ?? "",
      fixturesDir,
    ],
    {
      cwd: path.join(process.cwd(), "../backend"),
      encoding: "utf8",
      env: { ...process.env, CODETALK_DATA_DIR: dataDir },
    },
  );
  expect(seed.status, seed.stderr || seed.stdout).toBe(0);
  return JSON.parse(seed.stdout) as SeedResult;
}

async function responseSha256(request: APIRequestContext, url: string) {
  const response = await request.get(url);
  expect(response.ok(), `${url} should return 2xx`).toBeTruthy();
  return sha256(await response.body());
}

async function assertFrozenWorkflowUnchanged(
  request: APIRequestContext,
  fixture: HistoricalWorkflowFixture,
  beforeVersion: unknown,
) {
  const { workflow_id: workflowId, published_version_id: versionId } = fixture.workflow_header;
  const response = await request.get(`${backendBase}/api/workbench/workflows/${workflowId}/versions/${versionId}`);
  expect(response.ok()).toBeTruthy();
  expect(await response.json()).toEqual(beforeVersion);
}

async function previewAndCopyLegacyWorkflow(
  page: Page,
  request: APIRequestContext,
  fixture: HistoricalWorkflowFixture,
) {
  const { workflow_id: workflowId, published_version_id: versionId } = fixture.workflow_header;
  const before = await request.get(`${backendBase}/api/workbench/workflows/${workflowId}/versions/${versionId}`);
  expect(before.ok()).toBeTruthy();
  const frozenBefore = await before.json();

  await page.goto(`/workflows/${workflowId}/versions/${versionId}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByText("这是不可修改的发布快照", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "预览并复制为 V3" }).click();
  const preview = page.getByRole("region", { name: "V3 迁移预览" });
  await expect(preview).toBeVisible();
  await expect(preview).toContainText(`Schema ${fixture.workflow_version.authoring_graph.schema_version} → Schema 3`);
  await expect(preview).toContainText("原工作流版本保持只读且不变");
  await page.screenshot({
    path: evidencePath("screenshots", `history-artifacts-v${fixture.workflow_version.authoring_graph.schema_version}-preview.png`),
    fullPage: true,
  });

  await assertFrozenWorkflowUnchanged(request, fixture, frozenBefore);
  await preview.getByRole("button", { name: "确认创建 V3 副本" }).click();
  await expect(page).toHaveURL(/\/workflows\/wf_[^/?#]+\/designer$/, { timeout: 20_000 });
  await expect(page.getByRole("region", { name: "工作流画布" })).toBeVisible();
  await assertFrozenWorkflowUnchanged(request, fixture, frozenBefore);
}

test("Phase 7: frozen V1/V2 workflows and historical run artifacts remain viewable and byte-exact after preview/copy", async ({ page, request }) => {
  assertPhase7IsolatedRuntime();
  test.setTimeout(120_000);
  const seeded = seedFrozenHistory();
  const beforeArtifactHash = seeded.hashes["support/historical-results.json"];
  const taskBeforeResponse = await request.get(`${backendBase}/api/workbench/tasks/${taskId}`);
  expect(taskBeforeResponse.ok()).toBeTruthy();
  const historicalTaskBefore = await taskBeforeResponse.json();

  for (const fixture of seeded.fixtures) {
    await previewAndCopyLegacyWorkflow(page, request, fixture);
  }

  await page.goto(`/tasks/${taskId}/runs/${taskRunId}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "Historical source report" })).toBeVisible();
  await expect(page.getByText("report.md", { exact: true })).toBeVisible();
  await expect(page.getByText("Attempt 2", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "技术诊断" }).click();
  const diagnostics = page.getByRole("complementary", { name: "技术诊断" });
  await expect(diagnostics).toBeVisible();
  await diagnostics.getByText("运行快照", { exact: true }).click();
  await expect(diagnostics).toContainText("phase0_legacy_report");

  const support = page.locator("summary", { hasText: "支撑文件与输入快照" });
  await support.click();
  await page.getByRole("button", { name: "historical-results.json" }).click();
  const preview = page.getByRole("dialog", { name: "产物预览" });
  await expect(preview).toBeVisible();
  await expect(preview).toContainText("内容较长，预览已截断，请下载完整文件。");
  const previewText = await preview.locator("pre").innerText();
  expect(previewText).toContain('"id": 1');
  expect(previewText).not.toContain('"id": 101');
  const previewBounds = await preview.locator("pre").evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
    overflowY: getComputedStyle(element).overflowY,
  }));
  expect(previewBounds.overflowY).toMatch(/auto|scroll/);
  expect(previewBounds.scrollHeight).toBeGreaterThan(previewBounds.clientHeight);
  await page.screenshot({ path: evidencePath("screenshots", "history-artifacts-large-preview.png"), fullPage: true });

  const downloadPromise = page.waitForEvent("download");
  await preview.getByTitle("下载完整文件").click();
  const download = await downloadPromise;
  const downloadedPath = evidencePath("downloads", "historical-results.json");
  await download.saveAs(downloadedPath);
  expect(sha256(readFileSync(downloadedPath))).toBe(beforeArtifactHash);

  const artifactUrl = `${backendBase}/api/workbench/task-runs/${taskRunId}/artifacts/download/${largeArtifactPath}`;
  expect(await responseSha256(request, artifactUrl)).toBe(beforeArtifactHash);
  await expect(page.getByRole("button", { name: "关闭产物预览" })).toBeVisible();

  const taskAfterResponse = await request.get(`${backendBase}/api/workbench/tasks/${taskId}`);
  expect(taskAfterResponse.ok()).toBeTruthy();
  expect(await taskAfterResponse.json()).toEqual(historicalTaskBefore);

  const dataDir = process.env.CODETALK_PLAYWRIGHT_DATA_DIR!;
  for (const [name, beforeHash] of Object.entries(seeded.hashes)) {
    if (name.startsWith("workflow:")) {
      const [, workflowId, versionId] = name.split(":", 3);
      const version = await request.get(`${backendBase}/api/workbench/workflows/${workflowId}/versions/${versionId}`);
      expect(version.ok()).toBeTruthy();
      continue;
    }
    const currentPath = path.join(dataDir, "workbench", "task_runs", taskRunId, name);
    expect(sha256(readFileSync(currentPath)), `${name} changed after migration preview/copy`).toBe(beforeHash);
  }
  await page.screenshot({ path: evidencePath("screenshots", "history-artifacts-run-cockpit.png"), fullPage: true });
});
