import { expect, test } from "@playwright/test";

import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";
import {
  assertPhase5IsolatedRuntime,
  configureFixtureProvider,
  configureSchemaReportThroughUi,
  createProfessionalGovernanceWorkflowThroughUi,
  createReportWorkflowThroughUi,
  createSourceRepository,
  createWorkspaceThroughUi,
  dragConnection,
  phase5BackendBase,
  publishCurrentWorkflowThroughUi,
  runPublishedWorkflowThroughUi,
  selectValidationProfileThroughUi,
  v3Axis,
} from "./support/phase5-v3-browser-helpers";

assertPhase5IsolatedRuntime();
assertCanMutatePublicRuntime({ env: process.env, flowName: "Phase 5 V3 governance browser acceptance" });

test("explicit validator blocks publish until the user selects a declared artifact", async ({ page }) => {
  test.setTimeout(120_000);
  const stamp = Date.now();

  await page.goto("/workflows/new", { waitUntil: "domcontentloaded" });
  await page.getByLabel("工作流名称").fill(`Phase 5 empty validator ${stamp}`);
  await page.getByTestId("workflow-template-free_source_analysis").check();
  await page.getByRole("button", { name: "创建并打开画布" }).click();
  await expect(page).toHaveURL(/\/workflows\/[^/?#]+\/designer$/, { timeout: 20_000 });

  const palette = page.getByRole("complementary", { name: "节点库" });
  await palette.getByTestId("workflow-palette-validator").dblclick();
  const inspector = page.getByRole("complementary", { name: "节点属性" });
  await expect(inspector.getByRole("alert")).toHaveText(
    "请至少选择一个已声明交付件，否则无法发布。",
  );

  await page.locator("header").getByRole("button", { name: "发布" }).click();
  await expect(page).toHaveURL(/\/workflows\/[^/?#]+\/designer$/);
  await expect(page.getByText("发现 1 个阻断问题", { exact: true })).toBeVisible();
  await expect(page.locator(".ct-v2-problem-list")).toContainText("未选择验收交付件");
  await expect(page.locator(".ct-v2-problem-list")).toContainText(
    "Validator 至少选择一个已声明交付件",
  );

  await inspector
    .getByRole("group", { name: "验收交付件" })
    .getByRole("checkbox", { name: /分析报告/ })
    .check();
  await expect(inspector.getByRole("alert")).toHaveCount(0);
  await page.locator("header").getByRole("button", { name: "保存" }).click();
  await expect(page.getByTestId("workflow-save-status")).toHaveText("已保存", { timeout: 20_000 });
  await page.locator("header").getByRole("button", { name: "预览执行计划" }).click();
  await expect(page.getByTestId("workflow-plan-handler-artifact_exists").first()).toBeVisible();
  await page.locator("header").getByRole("button", { name: "发布" }).click();
  await expect(page).toHaveURL(/\/workflows\/[^/?#]+\/versions$/, { timeout: 30_000 });
});

test("professional profile blocks an incompatible role output until the user fixes its JSON contract", async ({ page }) => {
  test.setTimeout(120_000);
  const stamp = Date.now();
  const workflowName = `Phase 5 professional output gate ${stamp}`;

  await page.goto("/workflows/new", { waitUntil: "domcontentloaded" });
  await page.getByLabel("工作流名称").fill(workflowName);
  await page.getByTestId("workflow-template-free_source_analysis").check();
  await page.getByRole("button", { name: "创建并打开画布" }).click();
  await expect(page).toHaveURL(/\/workflows\/[^/?#]+\/designer$/, { timeout: 20_000 });
  await selectValidationProfileThroughUi(page, "storage_test_design");

  const canvas = page.getByRole("region", { name: "工作流画布" });
  const inspector = page.getByRole("complementary", { name: "节点属性" });
  await canvas.getByRole("article", { name: /分析报告.*输出节点/ }).click();
  const roles = inspector.getByRole("group", { name: "验收角色" });
  await roles.getByRole("checkbox", { name: "SFMEA" }).check();
  await roles.getByRole("checkbox", { name: "黑盒测试" }).check();
  await page.locator("header").getByRole("button", { name: "保存" }).click();
  await expect(page.getByTestId("workflow-save-status")).toHaveText("已保存", { timeout: 20_000 });

  await page.locator("header").getByRole("button", { name: "发布" }).click();
  await expect(page).toHaveURL(/\/workflows\/[^/?#]+\/designer$/);
  await expect(page.getByText("发现 2 个阻断问题", { exact: true })).toBeVisible();
  await expect(page.locator(".ct-v2-problem-list")).toContainText(
    "交付件“分析报告”必须使用 JSON 数据格式",
  );

  await canvas.getByRole("article", { name: /分析报告.*输出节点/ }).click();
  await inspector.getByLabel("输出类型").selectOption("json");
  await inspector.getByLabel("结构规则").selectOption("array");
  await inspector.getByLabel("文件名").fill("custom-professional-result.json");
  await page.locator("header").getByRole("button", { name: "保存" }).click();
  await expect(page.getByTestId("workflow-save-status")).toHaveText("已保存", { timeout: 20_000 });
  await page.locator("header").getByRole("button", { name: "发布" }).click();
  await expect(page).toHaveURL(/\/workflows\/[^/?#]+\/versions$/, { timeout: 30_000 });
});

test("required governance evidence can be disconnected and reconnected with real mouse gestures while handler ports stay locked", async ({ page, request }) => {
  test.setTimeout(240_000);
  const stamp = Date.now();
  const workflowName = `Phase 5 governance reconnect ${stamp}`;
  const repo = createSourceRepository(workflowName);
  const provider = await configureFixtureProvider(request, repo, "professional-evidence");
  try {
    await createProfessionalGovernanceWorkflowThroughUi(page, {
      name: workflowName,
      providerRef: provider.id,
    });
    const canvas = page.getByRole("region", { name: "工作流画布" });
    const governance = canvas.getByRole("article", { name: /存储测试专业设计.*governance节点/i });
    await governance.click();
    const inspector = page.getByRole("complementary", { name: "节点属性" });
    await expect(inspector).toContainText("处理器端口由系统维护");
    await expect(governance.getByLabel(/输入端口 源码证据，类型 artifact/i)).toBeVisible();
    await expect(governance.getByLabel(/输出端口 SFMEA 风险清单，类型 artifact/i)).toBeVisible();
    await expect(inspector.getByLabel(/输入端口 1/)).toHaveCount(0);
    await expect(inspector.getByRole("button", { name: /删除输入端口/ })).toHaveCount(0);
    await expect(inspector.getByRole("button", { name: "增加输入端口" })).toHaveCount(0);
    await inspector.getByRole("button", { name: "关闭属性面板" }).click();

    const evidenceEdgeLabel = page.getByText(
      "源码证据 · artifact → 源码证据 · artifact",
      { exact: true },
    );
    const edgeId = await evidenceEdgeLabel.getAttribute("data-edge-id");
    expect(edgeId).toBeTruthy();
    const edge = canvas.locator(`.react-flow__edge[data-id="${edgeId}"]`);
    await edge.locator(".react-flow__edge-interaction").click({ force: true });
    await canvas.getByTitle("删除所选").click();
    await expect(evidenceEdgeLabel).toHaveCount(0);
    await page.locator("header").getByRole("button", { name: "保存" }).click();
    await expect(page.getByTestId("workflow-save-status")).toHaveText("已保存", { timeout: 20_000 });

    await page.locator("header").getByRole("button", { name: "发布" }).click();
    await expect(page).toHaveURL(/\/workflows\/[^/?#]+\/designer$/);
    await expect(page.locator(".ct-v2-problem-list")).toContainText(
      "必填输入“源码证据”未连接",
    );

    const agent = canvas.getByRole("article", { name: /源码分析.*Agent节点/ });
    await dragConnection(
      page,
      agent.getByLabel("输出端口 源码证据，类型 artifact"),
      governance.getByLabel(/输入端口 源码证据，类型 artifact/i),
    );
    await expect(page.getByText(
      "源码证据 · artifact → 源码证据 · artifact",
      { exact: true },
    )).toBeVisible();
    await page.locator("header").getByRole("button", { name: "发布" }).click();
    await expect(page).toHaveURL(/\/workflows\/[^/?#]+\/versions$/, { timeout: 30_000 });
  } finally {
    await provider.remove();
  }
});

test("json schema validator blocks publish until its declared output schema is configured through UI", async ({ page }) => {
  test.setTimeout(120_000);
  const stamp = Date.now();
  const workflowName = `Phase 5 validator switch ${stamp}`;

  await page.goto("/workflows/new", { waitUntil: "domcontentloaded" });
  await page.getByLabel("工作流名称").fill(workflowName);
  await page.getByTestId("workflow-template-free_source_analysis").check();
  await page.getByRole("button", { name: "创建并打开画布" }).click();
  await expect(page).toHaveURL(/\/workflows\/[^/?#]+\/designer$/, { timeout: 20_000 });

  const canvas = page.getByRole("region", { name: "工作流画布" });
  const palette = page.getByRole("complementary", { name: "节点库" });
  await palette.getByTestId("workflow-palette-validator").dblclick();
  const inspector = page.getByRole("complementary", { name: "节点属性" });
  await expect(inspector).toBeVisible();
  await inspector.getByLabel("节点名称").fill("JSON 结构校验");
  await expect(inspector.getByLabel("Validator")).toHaveValue("artifact_exists");
  await inspector.getByLabel("Validator").selectOption("json_schema");
  await expect(inspector.getByLabel("Validator")).toHaveValue("json_schema", { timeout: 20_000 });
  await inspector
    .getByRole("group", { name: "验收交付件" })
    .getByRole("checkbox", { name: /分析报告/ })
    .check();

  await page.locator("header").getByRole("button", { name: "保存" }).click();
  await expect(page.getByTestId("workflow-save-status")).toHaveText("已保存", { timeout: 20_000 });
  await page.reload({ waitUntil: "domcontentloaded" });
  await canvas.getByRole("article", { name: /validator节点/i }).last().click();
  await expect(inspector.getByLabel("Validator")).toHaveValue("json_schema");

  await page.locator("header").getByRole("button", { name: "预览执行计划" }).click();
  await expect(page.locator(".ct-v2-problem-list")).toContainText(
    "JSON 结构校验所验收的交付件“分析报告”缺少有效的 JSON Schema；请在输出节点选择 JSON 类型并配置结构规则。",
  );
  await expect(page.getByTestId("workflow-plan-handler-json_schema")).toHaveCount(0);
  await page.locator("header").getByRole("button", { name: "发布" }).click();
  await expect(page).toHaveURL(/\/workflows\/[^/?#]+\/designer$/);
  await expect(page.getByText("发现 1 个阻断问题", { exact: true })).toBeVisible();

  await canvas.getByRole("article", { name: /分析报告.*输出节点/ }).click();
  await inspector.getByLabel("输出类型").selectOption("json");
  await inspector.getByLabel("结构规则").selectOption("object");
  await inspector.getByLabel("文件名").fill("report.json");
  await page.locator("header").getByRole("button", { name: "保存" }).click();
  await expect(page.getByTestId("workflow-save-status")).toHaveText("已保存", { timeout: 20_000 });
  await page.reload({ waitUntil: "domcontentloaded" });
  await canvas.getByRole("article", { name: /分析报告.*输出节点/ }).click();
  await expect(inspector.getByLabel("输出类型")).toHaveValue("json");
  await expect(inspector.getByLabel("结构规则")).toHaveValue("object");
  await expect(inspector.getByLabel("文件名")).toHaveValue("report.json");

  await page.locator("header").getByRole("button", { name: "预览执行计划" }).click();
  await expect(page.getByTestId("workflow-plan-handler-json_schema")).toBeVisible();
  await page.locator("header").getByRole("button", { name: "发布" }).click();
  await expect(page).toHaveURL(/\/workflows\/[^/?#]+\/versions$/, { timeout: 30_000 });
});

test("generic report-only workflow has no SFMEA or Test Activity ghost output", async ({ page, request }) => {
  test.setTimeout(120_000);
  const stamp = Date.now();
  const workflowName = `Phase 5 generic report ${stamp}`;
  const workspaceName = `Phase 5 generic workspace ${stamp}`;
  const repo = createSourceRepository(workspaceName);
  const provider = await configureFixtureProvider(request, repo, "report");
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await createWorkspaceThroughUi(page, workspaceName, repo);
    await createReportWorkflowThroughUi(page, workflowName, provider.id);
    await publishCurrentWorkflowThroughUi(page);
    const runId = await runPublishedWorkflowThroughUi(page, {
      workflowName,
      taskName: `Phase 5 generic task ${stamp}`,
      workspaceName,
      inspectOutputs: async (outputPage) => {
        await expect(outputPage.getByRole("textbox", { name: /分析报告 文件名/ })).toHaveValue("report.md");
        await expect(outputPage.getByText("sfmea.json", { exact: true })).toHaveCount(0);
        await expect(outputPage.getByText("black_box_cases.json", { exact: true })).toHaveCount(0);
        await expect(outputPage.getByText("risk-register.json", { exact: true })).toHaveCount(0);
        await expect(outputPage.getByText("test-matrix.json", { exact: true })).toHaveCount(0);
        await expect(outputPage.getByText(/test_activity_contract/i)).toHaveCount(0);
      },
    });

    await expect(v3Axis(page, "执行").getByText("已完成", { exact: true })).toBeVisible({ timeout: 60_000 });
    await expect(v3Axis(page, "产物校验").getByText("已通过", { exact: true })).toBeVisible();
    await expect(v3Axis(page, "专业治理").getByText("未请求", { exact: true })).toBeVisible();
    await expect(v3Axis(page, "交付").getByText("可交付", { exact: true })).toBeVisible();
    await expect(page.locator(".ct-v2-run-deliverables").getByRole("button", { name: /report\.md/ })).toBeVisible();
    await expect(page.getByText("sfmea.json", { exact: true })).toHaveCount(0);
    await expect(page.getByText("black_box_cases.json", { exact: true })).toHaveCount(0);
    await expect(page.getByText("risk-register.json", { exact: true })).toHaveCount(0);
    await expect(page.getByText("test-matrix.json", { exact: true })).toHaveCount(0);
    await expect(page.getByText(/test_activity_contract/i)).toHaveCount(0);

    const artifactsResponse = await request.get(`${phase5BackendBase}/api/workbench/task-runs/${runId}/artifacts`);
    expect(artifactsResponse.ok()).toBeTruthy();
    const manifest = await artifactsResponse.json() as { artifacts: Array<{ relative_path: string; audience: string }> };
    const deliverables = manifest.artifacts.filter((artifact) => artifact.audience === "deliverable");
    expect(deliverables.map((artifact) => artifact.relative_path)).toEqual([expect.stringMatching(/report\.md$/)]);
    expect(
      manifest.artifacts.map((artifact) => artifact.relative_path).filter((artifact) =>
        /sfmea|black_box|risk-register|test-matrix|test_activity/i.test(artifact),
      ),
    ).toEqual([]);
  } finally {
    await provider.remove();
  }
});

test("professional storage profile visibly expands into validation plan nodes and declared artifacts", async ({ page, request }) => {
  test.setTimeout(90_000);
  const stamp = Date.now();
  const workflowName = `Phase 5 professional storage ${stamp}`;
  const repo = createSourceRepository(workflowName);
  const provider = await configureFixtureProvider(request, repo, "professional-evidence");
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await createProfessionalGovernanceWorkflowThroughUi(page, {
      name: workflowName,
      providerRef: provider.id,
    });
    const canvas = page.getByRole("region", { name: "工作流画布" });
    await expect(canvas).toBeVisible();

    await canvas.getByRole("article", { name: /SFMEA 风险清单.*输出节点/ }).click();
    await expect(page.getByRole("complementary", { name: "节点属性" }).getByLabel("文件名")).toHaveValue("risk-register.json");
    await canvas.getByRole("article", { name: /黑盒测试用例.*输出节点/ }).click();
    await expect(page.getByRole("complementary", { name: "节点属性" }).getByLabel("文件名")).toHaveValue("test-matrix.json");

    await expect(
      canvas.getByTestId("workflow-palette-governance"),
      "Phase 5 blocker: the public node registry must expose registered Governance handlers",
    ).toBeVisible();
    await page.locator("header").getByRole("button", { name: "预览执行计划" }).click();
    await expect(page.getByTestId("workflow-plan-handler-sfmea")).toContainText("SFMEA 验收");
    await expect(page.getByTestId("workflow-plan-handler-black_box")).toContainText("黑盒测试验收");
  } finally {
    await provider.remove();
  }
});

test("explicit storage governance blocks an incompatible semantic output until UI correction", async ({ page, request }) => {
  test.setTimeout(120_000);
  const stamp = Date.now();
  const workflowName = `Phase 5 governance output gate ${stamp}`;
  const repo = createSourceRepository(workflowName);
  const provider = await configureFixtureProvider(request, repo, "professional-evidence");
  try {
    await createProfessionalGovernanceWorkflowThroughUi(page, {
      name: workflowName,
      providerRef: provider.id,
    });
    await page.getByLabel("验收模式").selectOption("none");
    const canvas = page.getByRole("region", { name: "工作流画布" });
    const inspector = page.getByRole("complementary", { name: "节点属性" });
    await canvas.getByRole("article", { name: /SFMEA 风险清单.*输出节点/ }).click();
    await inspector.getByLabel("输出类型").selectOption("markdown");
    await inspector.getByLabel("文件名").fill("arbitrary-risk-result.data");
    await page.locator("header").getByRole("button", { name: "保存" }).click();
    await expect(page.getByTestId("workflow-save-status")).toHaveText("已保存", { timeout: 20_000 });

    await page.locator("header").getByRole("button", { name: "发布" }).click();
    await expect(page).toHaveURL(/\/workflows\/[^/?#]+\/designer$/);
    await expect(page.locator(".ct-v2-problem-list")).toContainText(
      "交付件“SFMEA 风险清单”必须使用 JSON 数据格式",
    );

    await canvas.getByRole("article", { name: /SFMEA 风险清单.*输出节点/ }).click();
    await inspector.getByLabel("输出类型").selectOption("json");
    await inspector.getByLabel("结构规则").selectOption("array");
    await inspector.getByLabel("文件名").fill("arbitrary-risk-result.data");
    await page.locator("header").getByRole("button", { name: "保存" }).click();
    await expect(page.getByTestId("workflow-save-status")).toHaveText("已保存", { timeout: 20_000 });
    await page.locator("header").getByRole("button", { name: "发布" }).click();
    await expect(page).toHaveURL(/\/workflows\/[^/?#]+\/versions$/, { timeout: 30_000 });
  } finally {
    await provider.remove();
  }
});

test("explicit professional flow consumes Agent evidence and delivers validated storage artifacts", async ({ page, request }) => {
  test.setTimeout(150_000);
  const stamp = Date.now();
  const workflowName = `Phase 5 professional delivery ${stamp}`;
  const workspaceName = `Phase 5 professional delivery workspace ${stamp}`;
  const repo = createSourceRepository(workspaceName);
  const provider = await configureFixtureProvider(request, repo, "professional-evidence");
  try {
    await createWorkspaceThroughUi(page, workspaceName, repo);
    await createProfessionalGovernanceWorkflowThroughUi(page, {
      name: workflowName,
      providerRef: provider.id,
    });
    await page.locator("header").getByRole("button", { name: "预览执行计划" }).click();
    await expect(page.getByTestId("workflow-plan-handler-storage_test_design")).toContainText("存储测试专业设计");
    await publishCurrentWorkflowThroughUi(page);
    const runId = await runPublishedWorkflowThroughUi(page, {
      workflowName,
      taskName: `Phase 5 professional delivery task ${stamp}`,
      workspaceName,
      inspectOutputs: async (outputPage) => {
        await expect(outputPage.getByRole("textbox", { name: /源码证据 文件名/ })).toHaveValue("verified-source-evidence.json");
        await expect(outputPage.getByRole("textbox", { name: /SFMEA 风险清单 文件名/ })).toHaveValue("risk-register.json");
        await expect(outputPage.getByRole("textbox", { name: /黑盒测试用例 文件名/ })).toHaveValue("test-matrix.json");
      },
    });

    await expect(v3Axis(page, "执行").getByText("已完成", { exact: true })).toBeVisible({ timeout: 90_000 });
    await expect(v3Axis(page, "产物校验").getByText("已通过", { exact: true })).toBeVisible();
    await expect(v3Axis(page, "专业治理").getByText("已通过", { exact: true })).toBeVisible();
    await expect(v3Axis(page, "交付").getByText("可交付", { exact: true })).toBeVisible();
    const artifactsResponse = await request.get(`${phase5BackendBase}/api/workbench/task-runs/${runId}/artifacts`);
    expect(artifactsResponse.ok()).toBeTruthy();
    const manifest = await artifactsResponse.json() as { artifacts: Array<{ relative_path: string; audience: string }> };
    const deliverables = manifest.artifacts
      .filter((artifact) => artifact.audience === "deliverable")
      .map((artifact) => artifact.relative_path);
    expect(deliverables).toEqual(expect.arrayContaining([
      expect.stringMatching(/verified-source-evidence\.json$/),
      expect.stringMatching(/risk-register\.json$/),
      expect.stringMatching(/test-matrix\.json$/),
    ]));
  } finally {
    await provider.remove();
  }
});

test("cockpit reports schema validation failure without relabeling it as execution or governance failure", async ({ page, request }) => {
  test.setTimeout(120_000);
  const stamp = Date.now();
  const workflowName = `Phase 5 invalid artifact ${stamp}`;
  const workspaceName = `Phase 5 invalid artifact workspace ${stamp}`;
  const repo = createSourceRepository(workspaceName);
  const provider = await configureFixtureProvider(request, repo, "invalid-schema");
  try {
    await createWorkspaceThroughUi(page, workspaceName, repo);
    await createReportWorkflowThroughUi(page, workflowName, provider.id);
    await selectValidationProfileThroughUi(page, "schema");
    await configureSchemaReportThroughUi(page);
    await publishCurrentWorkflowThroughUi(page);
    await runPublishedWorkflowThroughUi(page, {
      workflowName,
      taskName: `Phase 5 invalid artifact task ${stamp}`,
      workspaceName,
      inspectOutputs: async (outputPage) => {
        await expect(outputPage.getByRole("textbox", { name: /分析报告 文件名/ })).toHaveValue("report.json");
      },
    });

    await expect(v3Axis(page, "执行").getByText("已完成", { exact: true })).toBeVisible({ timeout: 60_000 });
    await expect(v3Axis(page, "产物校验").getByText("未通过", { exact: true })).toBeVisible();
    await expect(v3Axis(page, "专业治理").getByText("未请求", { exact: true })).toBeVisible();
    await expect(v3Axis(page, "交付").getByText("已阻断", { exact: true })).toBeVisible();
  } finally {
    await provider.remove();
  }
});

test("cockpit blocks delivery when professional evidence prevents downstream validation", async ({ page, request }) => {
  test.setTimeout(120_000);
  const stamp = Date.now();
  const workflowName = `Phase 5 failed governance ${stamp}`;
  const workspaceName = `Phase 5 failed governance workspace ${stamp}`;
  const repo = createSourceRepository(workspaceName);
  const provider = await configureFixtureProvider(request, repo, "invalid-governance");
  try {
    await createWorkspaceThroughUi(page, workspaceName, repo);
    await createProfessionalGovernanceWorkflowThroughUi(page, {
      name: workflowName,
      providerRef: provider.id,
    });
    await publishCurrentWorkflowThroughUi(page);
    await runPublishedWorkflowThroughUi(page, {
      workflowName,
      taskName: `Phase 5 failed governance task ${stamp}`,
      workspaceName,
      inspectOutputs: async (outputPage) => {
        await expect(outputPage.getByRole("textbox", { name: /源码证据 文件名/ })).toHaveValue("verified-source-evidence.json");
        await expect(outputPage.getByRole("textbox", { name: /SFMEA 风险清单 文件名/ })).toHaveValue("risk-register.json");
        await expect(outputPage.getByRole("textbox", { name: /黑盒测试用例 文件名/ })).toHaveValue("test-matrix.json");
      },
    });

    await expect(v3Axis(page, "执行").getByText("已完成", { exact: true })).toBeVisible({ timeout: 60_000 });
    await expect(v3Axis(page, "产物校验")).toContainText("未通过", { timeout: 20_000 });
    await expect(v3Axis(page, "专业治理")).toContainText("未通过", { timeout: 20_000 });
    await expect(v3Axis(page, "交付")).toContainText("已阻断", { timeout: 20_000 });
  } finally {
    await provider.remove();
  }
});
