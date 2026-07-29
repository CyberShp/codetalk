import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type APIRequestContext, type Page, type Request } from "@playwright/test";

import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

const frontendPort = process.env.CODETALK_FRONTEND_PORT ?? "3003";
const backendPort = process.env.CODETALK_BACKEND_PORT ?? "3004";
const backendBase = `http://localhost:${backendPort}`;
const evidenceDir = path.join(
  process.env.CODETALK_E2E_ARTIFACT_DIR ?? process.env.CODETALK_PLAYWRIGHT_DATA_DIR ?? "/Volumes/Media/codetalk-e2e-artifacts/phase7",
  "evidence",
);

assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "Phase 7 migration and template browser acceptance",
});

function assertPhase7IsolatedRuntime() {
  if (frontendPort !== "3233" || backendPort !== "3234") {
    throw new Error(
      "Phase 7 browser acceptance must run on isolated ports 3233/3234. " +
        "Set CODETALK_FRONTEND_PORT=3233 CODETALK_BACKEND_PORT=3234 CODETALK_REUSE_EXISTING_SERVER=0.",
    );
  }
}

function evidencePath(name: string) {
  mkdirSync(evidenceDir, { recursive: true });
  return path.join(evidenceDir, name);
}

async function expectNoViewportOverflow(page: Page) {
  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth, `page overflowed horizontally: ${JSON.stringify(dimensions)}`).toBeLessThanOrEqual(
    dimensions.clientWidth + 1,
  );
}

async function createDefaultFreeSourceWorkflow(page: Page, name: string) {
  const createResponse = page.waitForResponse((response) => {
    return response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/workbench/workflows/new";
  });
  await page.getByLabel("工作流名称").fill(name);
  await page.getByRole("button", { name: "创建并打开画布" }).click();
  const response = await createResponse;
  expect(response.status()).toBe(201);
  const payload = await response.json() as {
    draft: { authoring_graph: { schema_version: number; settings: { validation_profile: string } } };
  };
  expect(payload.draft.authoring_graph.schema_version).toBe(3);
  expect(payload.draft.authoring_graph.settings.validation_profile).toBe("artifact_only");

  await expect(page).toHaveURL(/\/workflows\/[^/?#]+\/designer$/, { timeout: 20_000 });
  await expect(page.getByRole("region", { name: "工作流画布" })).toBeVisible();
  await expect(page.getByLabel("验收模式")).toHaveValue("artifact_only");
}

type HistoricalFixture = {
  workflow_header: { workflow_id: string; published_version_id: string };
  workflow_version: { authoring_graph: unknown };
};

function seedHistoricalV1AndV2Fixtures(): HistoricalFixture[] {
  const dataDir = process.env.CODETALK_PLAYWRIGHT_DATA_DIR;
  expect(dataDir, "isolated Playwright data directory must be available").toBeTruthy();
  const fixturesDir = path.join(
    process.cwd(),
    "../backend/tests/fixtures/harness_workflow_refactor",
  );
  const seeded = spawnSync(
    process.env.CODETALK_BACKEND_PYTHON ?? "python3.11",
    [
      "-c",
      [
        "import json, sqlite3, sys",
        "from pathlib import Path",
        "from app.services.workflow_version_store import WorkflowVersionStore",
        "data_dir = Path(sys.argv[1])",
        "fixtures_dir = Path(sys.argv[2])",
        "db_path = data_dir / 'workbench' / 'workflows.db'",
        "WorkflowVersionStore(db_path).initialize_and_migrate()",
        "for name in ('v1-published-workflow.json', 'v2-published-workflow.json'):",
        " fixture = json.loads((fixtures_dir / name).read_text(encoding='utf-8'))",
        " header, version = fixture['workflow_header'], fixture['workflow_version']",
        " serialized = [json.dumps(version[field], ensure_ascii=False, sort_keys=True) for field in ('authoring_graph', 'compiled_definition', 'compiled_plan', 'validation')]",
        " with sqlite3.connect(db_path) as db:",
        "  db.execute('INSERT INTO workflow_headers(workflow_id, name, description, status, published_version_id, current_draft_version_id, created_at, updated_at, archived_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', tuple(header[field] for field in ('workflow_id', 'name', 'description', 'status', 'published_version_id', 'current_draft_version_id', 'created_at', 'updated_at', 'archived_at')))",
        "  db.execute('INSERT INTO workflow_versions(version_id, workflow_id, version_number, state, authoring_graph_json, compiled_definition_json, compiled_plan_json, validation_json, based_on_version_id, created_at, updated_at, published_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (version['version_id'], version['workflow_id'], version['version_number'], version['state'], *serialized, version['based_on_version_id'], version['created_at'], version['updated_at'], version['published_at']))",
      ].join("\n"),
      dataDir ?? "",
      fixturesDir,
    ],
    {
      cwd: path.join(process.cwd(), "../backend"),
      encoding: "utf8",
      env: process.env,
    },
  );
  expect(seeded.status, seeded.stderr || seeded.stdout).toBe(0);

  return ["v1-published-workflow.json", "v2-published-workflow.json"].map((name) => {
    return JSON.parse(readFileSync(path.join(fixturesDir, name), "utf8")) as HistoricalFixture;
  });
}

async function expectHistoricalPreviewWithoutWrite(
  page: Page,
  request: APIRequestContext,
  workflowId: string,
  versionId: string,
  navigate: () => Promise<void>,
) {
  const encodedWorkflowId = encodeURIComponent(workflowId);
  const encodedVersionId = encodeURIComponent(versionId);
  const workflowsBefore = await request.get(`${backendBase}/api/workbench/workflows`);
  expect(workflowsBefore.ok()).toBeTruthy();
  const headersBefore = await workflowsBefore.json();
  const writeRequests: string[] = [];
  const captureWrite = (outgoing: Request) => {
    if (outgoing.method() !== "POST" || new URL(outgoing.url()).origin !== backendBase) return;
    const pathname = new URL(outgoing.url()).pathname;
    if (
      pathname === `/api/workbench/workflows/${encodedWorkflowId}/versions` ||
      pathname === "/api/workbench/workflows/new" ||
      pathname.endsWith("/copy") ||
      pathname.endsWith("/copy-to-v3")
    ) {
      writeRequests.push(pathname);
    }
  };

  page.on("request", captureWrite);
  try {
    await navigate();
    await expect(page).toHaveURL(
      new RegExp(`/workflows/${encodedWorkflowId}/versions/${encodedVersionId}$`),
    );
    await expect(page.getByText("这是不可修改的发布快照", { exact: false })).toBeVisible();
  } finally {
    page.off("request", captureWrite);
  }

  expect(writeRequests).toEqual([]);
  const workflowsAfter = await request.get(`${backendBase}/api/workbench/workflows`);
  expect(workflowsAfter.ok()).toBeTruthy();
  expect(await workflowsAfter.json()).toEqual(headersBefore);
}

test("Phase 7: server-owned six-template chooser defaults to free source analysis and persists a V3 canvas on desktop", async ({ page }) => {
  assertPhase7IsolatedRuntime();
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1440, height: 900 });

  await page.goto("/workflows/new", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("workflow-canvas-create-dialog")).toBeVisible();
  await expect(page.getByTestId("workflow-template-free_source_analysis")).toBeChecked();
  for (const template of [
    "blank",
    "free_source_analysis",
    "source_with_optional_design",
    "change_impact_analysis",
    "multi_agent_analysis",
    "formal_storage_test_design",
  ]) {
    await expect(page.getByTestId(`workflow-template-${template}`)).toBeVisible();
  }
  await expect(page.getByTestId("workflow-template-formal_storage_test_design").locator("xpath=.."))
    .toContainText("正式存储测试设计");
  await expect(page.getByTestId("workflow-template-formal_storage_test_design").locator("xpath=.."))
    .toContainText("专业");
  await expectNoViewportOverflow(page);

  await createDefaultFreeSourceWorkflow(page, `Phase 7 desktop ${Date.now()}`);
  await expect(page.getByRole("article", { name: /源码工作区.*输入节点/ })).toBeVisible();
  await expect(page.getByRole("article", { name: /分析报告.*输出节点/ })).toBeVisible();
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByRole("region", { name: "工作流画布" })).toBeVisible();
  await expect(page.getByLabel("验收模式")).toHaveValue("artifact_only");
  await expectNoViewportOverflow(page);
  await page.screenshot({ path: evidencePath("phase7-v3-canvas-desktop.png"), fullPage: true });
});

test("Phase 7: mobile template chooser remains complete, creates the default V3 canvas, and has no horizontal page overflow", async ({ page }) => {
  assertPhase7IsolatedRuntime();
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 390, height: 844 });

  await page.goto("/workflows/new", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("workflow-template-free_source_analysis")).toBeChecked();
  await expect(page.getByTestId("workflow-template-formal_storage_test_design")).toBeVisible();
  await expect(page.getByTestId("workflow-template-formal_storage_test_design").locator("xpath=.."))
    .toContainText("正式存储测试设计");
  await expectNoViewportOverflow(page);

  await createDefaultFreeSourceWorkflow(page, `Phase 7 mobile ${Date.now()}`);
  await expect(page.getByTestId("workflow-mobile-palette-toggle")).toBeVisible();
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByRole("region", { name: "工作流画布" })).toBeVisible();
  await expectNoViewportOverflow(page);
  await page.screenshot({ path: evidencePath("phase7-v3-canvas-mobile.png"), fullPage: true });
});

test("Phase 7: frozen V1 and V2 entry points show a no-write migration preview before explicit V3 copy", async ({ page, request }) => {
  assertPhase7IsolatedRuntime();
  test.setTimeout(120_000);
  const fixtures = seedHistoricalV1AndV2Fixtures();

  for (const fixture of fixtures) {
    const workflowId = fixture.workflow_header.workflow_id;
    const versionId = fixture.workflow_header.published_version_id;
    const schemaVersion = (fixture.workflow_version.authoring_graph as { schema_version: number }).schema_version;
    const beforeVersion = await request.get(`${backendBase}/api/workbench/workflows/${workflowId}/versions/${versionId}`);
    expect(beforeVersion.ok()).toBeTruthy();
    const frozenBefore = await beforeVersion.json();
    const workflowsBefore = await request.get(`${backendBase}/api/workbench/workflows`);
    expect(workflowsBefore.ok()).toBeTruthy();
    const headersBefore = await workflowsBefore.json();

    await page.setViewportSize({ width: 1440, height: 900 });
    await expectHistoricalPreviewWithoutWrite(page, request, workflowId, versionId, async () => {
      await page.goto("/workflows", { waitUntil: "domcontentloaded" });
      const row = page.locator("tr").filter({
        has: page.locator(`a[href="/workflows/${encodeURIComponent(workflowId)}"]`),
      });
      await expect(row).toBeVisible();
      await row.locator('button[title="查看 V3 迁移预览"]').click();
    });

    await expectHistoricalPreviewWithoutWrite(page, request, workflowId, versionId, async () => {
      await page.goto(`/workflows/${encodeURIComponent(workflowId)}/versions`, { waitUntil: "domcontentloaded" });
      await page.getByRole("button", { name: "查看 V3 迁移预览" }).click();
    });

    await expectHistoricalPreviewWithoutWrite(page, request, workflowId, versionId, async () => {
      await page.goto(`/workflows/${encodeURIComponent(workflowId)}/designer`, { waitUntil: "domcontentloaded" });
    });

    if (schemaVersion === 2) {
      await page.goto(
        `/workflows/${encodeURIComponent(workflowId)}/legacy?workflow=${encodeURIComponent(workflowId)}&version=${encodeURIComponent(versionId)}`,
        { waitUntil: "domcontentloaded" },
      );
      await expect(page.getByTestId("workflow-wizard-ready")).toBeVisible();
      await page.getByRole("button", { name: "查看 V3 迁移预览" }).click();
      await expect(page).toHaveURL(
        `/workflows/${encodeURIComponent(workflowId)}/versions/${encodeURIComponent(versionId)}`,
      );
    } else {
      await page.goto(`/workflows/${workflowId}/versions/${versionId}`, { waitUntil: "domcontentloaded" });
    }
    await expect(page.getByText("这是不可修改的发布快照", { exact: false })).toBeVisible();
    await page.getByRole("button", { name: "预览并复制为 V3" }).click();
    const preview = page.getByRole("region", { name: "V3 迁移预览" });
    await expect(preview).toBeVisible();
    await expect(preview).toContainText(`Schema ${schemaVersion} → Schema 3`);
    await expect(preview).toContainText("原工作流版本保持只读且不变");
    await page.screenshot({
      path: evidencePath(`phase7-${schemaVersion}-migration-preview.png`),
      fullPage: true,
    });

    const afterPreviewVersion = await request.get(`${backendBase}/api/workbench/workflows/${workflowId}/versions/${versionId}`);
    expect(afterPreviewVersion.ok()).toBeTruthy();
    expect(await afterPreviewVersion.json()).toEqual(frozenBefore);
    const workflowsAfterPreview = await request.get(`${backendBase}/api/workbench/workflows`);
    expect(workflowsAfterPreview.ok()).toBeTruthy();
    expect(await workflowsAfterPreview.json()).toEqual(headersBefore);

    await preview.getByRole("button", { name: "确认创建 V3 副本" }).click();
    await expect(page).toHaveURL(/\/workflows\/wf_[^/?#]+\/designer$/, { timeout: 20_000 });
    await expect(page.getByRole("region", { name: "工作流画布" })).toBeVisible();
    await expect(page.getByLabel("验收模式")).toHaveValue("artifact_only");

    const afterCopyVersion = await request.get(`${backendBase}/api/workbench/workflows/${workflowId}/versions/${versionId}`);
    expect(afterCopyVersion.ok()).toBeTruthy();
    expect(await afterCopyVersion.json()).toEqual(frozenBefore);
  }
});
