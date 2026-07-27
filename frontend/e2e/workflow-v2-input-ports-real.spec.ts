import { expect, test } from "@playwright/test";

import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "Workflow V2 typed input ports real E2E",
});

async function dragPort(
  page: import("@playwright/test").Page,
  source: import("@playwright/test").Locator,
  target: import("@playwright/test").Locator,
) {
  await expect(source).toBeVisible();
  await expect(target).toBeVisible();
  const sourceBox = await source.boundingBox();
  const targetBox = await target.boundingBox();
  expect(sourceBox).not.toBeNull();
  expect(targetBox).not.toBeNull();
  if (!sourceBox || !targetBox) return;
  await page.mouse.move(sourceBox.x + sourceBox.width / 2, sourceBox.y + sourceBox.height / 2);
  await page.mouse.down({ button: "left" });
  await page.mouse.move(targetBox.x + targetBox.width / 2, targetBox.y + targetBox.height / 2, { steps: 12 });
  await page.mouse.up({ button: "left" });
}

test("binds directory and file to distinct typed ports and rejects invalid drag immediately", async ({
  page,
  request,
}) => {
  test.setTimeout(60_000);
  const stamp = Date.now();
  const workflowId = `typed_ports_e2e_${stamp}`;
  const workflowName = `Typed Ports E2E ${stamp}`;
  const created = await request.post(`${backendBase}/api/workbench/workflows`, {
    data: {
      id: workflowId,
      name: workflowName,
      description: "真实鼠标验证多输入端口契约",
      authoring_graph: {
        schema_version: 2,
        workflow_id: workflowId,
        name: workflowName,
        description: "真实鼠标验证多输入端口契约",
        nodes: [
          {
            id: "repo",
            kind: "input",
            label: "源码工作区",
            position: { x: 60, y: 90 },
            config: { contract_id: "repo", label: "源码工作区", type: "directory", required: true, resolver: "local" },
          },
          {
            id: "design_doc",
            kind: "input",
            label: "开发设计文档",
            position: { x: 60, y: 290 },
            config: { contract_id: "design_doc", label: "开发设计文档", type: "file", required: false, resolver: "local" },
          },
          {
            id: "analyze",
            kind: "agent",
            label: "源码分析",
            position: { x: 430, y: 110 },
            config: {
              step_id: "analyze",
              goal: "基于源码工作区和开发设计文档分析指定对象。",
              provider: "builtin-llm",
              input_ports: [{ id: "repo_path", type: "directory", required: true }],
              output_ports: [{ id: "report", type: "markdown" }],
              mcp_profiles: [],
              skill_ids: [],
              required_artifacts: ["report.md"],
              timeout_sec: 900,
              failure_policy: "stop",
            },
          },
          {
            id: "type_probe",
            kind: "agent",
            label: "类型校验目标",
            position: { x: 430, y: 300 },
            config: {
              step_id: "type_probe",
              goal: "仅用于验证目录输入端口。",
              provider: "builtin-llm",
              input_ports: [{ id: "repo_path", type: "directory", required: false }],
              output_ports: [{ id: "done", type: "markdown" }],
              mcp_profiles: [],
              skill_ids: [],
              required_artifacts: ["probe.md"],
              timeout_sec: 900,
              failure_policy: "stop",
            },
          },
          {
            id: "report",
            kind: "output",
            label: "分析报告",
            position: { x: 800, y: 110 },
            config: { output_id: "report", label: "分析报告", type: "markdown", artifact: "report.md", required: true },
          },
          {
            id: "probe_report",
            kind: "output",
            label: "校验输出",
            position: { x: 800, y: 300 },
            config: { output_id: "probe_report", label: "校验输出", type: "markdown", artifact: "probe.md", required: false },
          },
        ],
        edges: [
          { id: "repo-analyze", kind: "data", source: { node_id: "repo", port_id: "value" }, target: { node_id: "analyze", port_id: "repo_path" } },
          { id: "analyze-report", kind: "data", source: { node_id: "analyze", port_id: "report" }, target: { node_id: "report", port_id: "value" } },
          { id: "probe-output", kind: "data", source: { node_id: "type_probe", port_id: "done" }, target: { node_id: "probe_report", port_id: "value" } },
        ],
        settings: { stop_on_error: true, max_parallelism: 1 },
      },
    },
  });
  expect(created.status()).toBe(201);

  try {
    await page.goto(`/workflows/${workflowId}`, { waitUntil: "domcontentloaded" });
    await page.getByRole("article", { name: /源码分析 Agent节点/ }).click();
    await expect(page.getByLabel("输入端口 1 名称")).toHaveValue("repo_path");

    await page.getByRole("button", { name: "增加输入端口" }).click();
    const secondName = page.getByLabel("输入端口 2 名称");
    await secondName.fill("design_doc");
    await secondName.press("Enter");
    await page.getByLabel("输入端口 2 类型").selectOption("file");
    // XYFlow ports are node-local labelled elements, not native buttons.
    await expect(page.getByRole("article", { name: /源码分析 Agent节点/ })
      .getByLabel("输入端口 design_doc，类型 file")).toBeVisible();

    await page.getByRole("button", { name: "增加输入端口" }).click();
    await expect(page.getByLabel("输入端口 3 名称")).toBeVisible();
    await page.getByLabel("输入端口 3 名称").fill("repo_path");
    await page.getByLabel("输入端口 3 名称").press("Enter");
    await expect(page.locator(".ct-v2-port-id-error")).toHaveText("输入名称已存在");
    await page.getByRole("button", { name: /删除输入端口 input_3/ }).click();
    await expect(page.getByLabel("输入端口 3 名称")).toHaveCount(0);

    await dragPort(
      page,
      page.getByRole("article", { name: /开发设计文档 输入节点/ })
        .getByLabel("输出端口 value，类型 file"),
      page.getByRole("article", { name: /源码分析 Agent节点/ })
        .getByLabel("输入端口 design_doc，类型 file"),
    );
    await expect(page.locator(".ct-v2-edge-label").filter({ hasText: "开发设计文档 · file → design_doc · file" })).toBeVisible();

    await page.getByRole("article", { name: /源码分析 Agent节点/ }).click();
    await secondName.click();
    await secondName.fill("");
    await expect(page.locator(".ct-v2-edge-label").filter({ hasText: "开发设计文档 · file → design_doc · file" })).toBeVisible();
    await secondName.pressSequentially("design_doc_v2");
    await expect(secondName).toHaveValue("design_doc_v2");
    await secondName.press("Enter");
    await expect(page.getByRole("article", { name: /源码分析 Agent节点/ })
      .getByLabel("输入端口 design_doc_v2，类型 file")).toBeVisible();
    await expect(page.locator(".ct-v2-edge-label").filter({ hasText: "开发设计文档 · file → design_doc_v2 · file" })).toBeVisible();

    await dragPort(
      page,
      page.getByRole("article", { name: /开发设计文档 输入节点/ })
        .getByLabel("输出端口 value，类型 file"),
      page.getByRole("article", { name: /源码分析 Agent节点/ })
        .getByLabel("输入端口 repo_path，类型 directory"),
    );
    await expect(page.locator(".ct-v2-connection-error")).toHaveText("该输入已绑定");

    await dragPort(
      page,
      page.getByRole("article", { name: /开发设计文档 输入节点/ })
        .getByLabel("输出端口 value，类型 file"),
      page.getByRole("article", { name: /类型校验目标 Agent节点/ })
        .getByLabel("输入端口 repo_path，类型 directory"),
    );
    await expect(page.locator(".ct-v2-connection-error")).toHaveText("不能连接：file 类型不能连接到 directory 输入");
    await expect(page.locator(".ct-v2-edge-label").filter({ hasText: "开发设计文档 · file → repo_path · directory" })).toHaveCount(0);

    await page.getByRole("button", { name: "保存并继续" }).click();
    await page.getByRole("button", { name: "验证" }).click();
    await expect(page.getByText("验证通过", { exact: true })).toBeVisible();
  } finally {
    await request.post(`${backendBase}/api/workbench/workflows/${workflowId}/archive`);
  }
});
