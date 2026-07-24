import { expect, test } from "@playwright/test";

import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "Workflow V2 xyflow browser E2E",
});

test("creates a workflow through the UI and uses the xyflow canvas with real mouse input", async ({ page }) => {
  test.setTimeout(90_000);
  const stamp = Date.now();

  await page.goto("/workflows/new", { waitUntil: "domcontentloaded" });
  await page.getByPlaceholder("例如：源码流程与 SFMEA 分析").fill(`画布交互回归 ${stamp}`);
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.getByRole("heading", { name: "定义输入" })).toBeVisible();

  for (let step = 0; step < 3; step += 1) {
    await page.getByRole("button", { name: "保存并继续" }).click();
  }
  await expect(page.getByRole("region", { name: "工作流画布" })).toBeVisible();

  const canvas = page.getByRole("region", { name: "工作流画布" });
  const canvasShell = page.locator(".ct-v2-canvas-shell");
  const flow = canvas.locator(".react-flow");
  await expect(flow).toBeVisible();
  await expect(canvasShell.getByTestId("workflow-palette-agent")).toBeVisible();

  const canvasBox = await flow.boundingBox();
  expect(canvasBox).not.toBeNull();
  if (!canvasBox) return;
  const paneStart = { x: canvasBox.x + canvasBox.width - 48, y: canvasBox.y + canvasBox.height - 48 };
  const transformBefore = await canvas.locator(".react-flow__viewport").getAttribute("style");
  await page.mouse.move(paneStart.x, paneStart.y);
  await page.mouse.down({ button: "left" });
  await page.mouse.move(paneStart.x - 120, paneStart.y - 68, { steps: 8 });
  await page.mouse.up({ button: "left" });
  await expect.poll(() => canvas.locator(".react-flow__viewport").getAttribute("style")).not.toBe(transformBefore);
  // Reopen the persisted draft before the independent node gesture. This
  // verifies both interactions through the user path without depending on a
  // transient browser viewport from the preceding pan assertion.
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(canvas).toBeVisible();
  await canvas.scrollIntoViewIfNeeded();

  const firstNode = canvas.locator(".react-flow__node-workflowNode").first();
  const nodeBefore = await firstNode.boundingBox();
  expect(nodeBefore).not.toBeNull();
  if (!nodeBefore) return;
  const dragHandle = firstNode.locator(".ct-v2-node-drag");
  const dragBox = await dragHandle.boundingBox();
  expect(dragBox).not.toBeNull();
  if (!dragBox) return;
  await page.mouse.move(dragBox.x + 42, dragBox.y + 20);
  await page.mouse.down({ button: "left" });
  await page.mouse.move(dragBox.x + 118, dragBox.y + 52, { steps: 10 });
  await page.mouse.up({ button: "left" });
  await expect.poll(async () => (await firstNode.boundingBox())?.x ?? 0).toBeGreaterThan(nodeBefore.x + 50);

  const beforeAdd = await canvas.locator(".react-flow__node-workflowNode").count();
  await canvasShell.getByTestId("workflow-palette-input").dblclick();
  await expect.poll(() => canvas.locator(".react-flow__node-workflowNode").count()).toBe(beforeAdd + 1);
  const designDocNode = canvas.locator(".react-flow__node-workflowNode").last();
  const designDocTestId = await designDocNode.locator("article").getAttribute("data-testid");
  expect(designDocTestId).toBeTruthy();
  const designDoc = canvas.getByTestId(designDocTestId!);
  await designDoc.click();
  const inspector = page.getByRole("complementary", { name: "节点属性" });
  await expect(inspector).toBeVisible();
  await expect(inspector.getByText("节点定义", { exact: true })).toBeVisible();
  await inspector.getByLabel("节点名称").fill("开发设计文档");
  await inspector.getByLabel("类型").selectOption("file");

  await canvas.getByTestId("workflow-node-analyze").click();
  await inspector.getByRole("button", { name: "增加输入端口" }).click();
  const thirdPortName = inspector.getByLabel("输入端口 3 名称");
  await thirdPortName.fill("design_doc");
  await thirdPortName.press("Enter");
  await inspector.getByLabel("输入端口 3 类型").selectOption("file");
  await expect(canvas.getByLabel("输入端口 design_doc，类型 file")).toBeVisible();
  await page.waitForTimeout(900);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(canvas).toBeVisible();
  await expect(canvas.getByLabel("输入端口 design_doc，类型 file")).toBeVisible();
  await canvas.scrollIntoViewIfNeeded();
  await canvas.getByTitle("适应画布").click();
  await page.waitForTimeout(250);

  await drag(
    page,
    designDoc.getByLabel("输出端口 value，类型 file"),
    canvas.getByTestId("workflow-node-analyze").getByLabel("输入端口 design_doc，类型 file"),
  );
  await expect(canvas.getByText("开发设计文档 · file → design_doc · file", { exact: true })).toBeVisible();

  await canvasShell.getByTestId("workflow-palette-agent").dblclick();
  await expect.poll(() => canvas.locator(".react-flow__node-workflowNode").count()).toBe(beforeAdd + 2);
  const typeProbeNode = canvas.locator(".react-flow__node-workflowNode").last();
  await typeProbeNode.click();
  await canvas.getByTitle("适应画布").click();
  await page.waitForTimeout(250);
  await drag(
    page,
    designDoc.getByLabel("输出端口 value，类型 file"),
    typeProbeNode.getByLabel("输入端口 repo_path，类型 directory"),
  );
  await expect(canvas.locator(".ct-v2-connection-error")).toHaveText("不能连接：file 类型不能连接到 directory 输入");

  await typeProbeNode.click();
  await page.keyboard.press("Delete");
  await expect.poll(() => canvas.locator(".react-flow__node-workflowNode").count()).toBe(beforeAdd + 1);
  await canvas.getByTitle("撤销").click();
  await expect.poll(() => canvas.locator(".react-flow__node-workflowNode").count()).toBe(beforeAdd + 2);
  await canvas.getByTitle("重做").click();
  await expect.poll(() => canvas.locator(".react-flow__node-workflowNode").count()).toBe(beforeAdd + 1);
});

test("box-selects multiple canvas nodes and batch deletes them through the visible toolbar", async ({ page }) => {
  test.setTimeout(90_000);
  const stamp = Date.now();

  await page.goto("/workflows/new", { waitUntil: "domcontentloaded" });
  await page.getByPlaceholder("例如：源码流程与 SFMEA 分析").fill(`多选交互回归 ${stamp}`);
  await page.getByRole("button", { name: "保存并继续" }).click();
  for (let step = 0; step < 3; step += 1) {
    await page.getByRole("button", { name: "保存并继续" }).click();
  }

  const canvas = page.getByRole("region", { name: "工作流画布" });
  const flow = canvas.locator(".react-flow");
  const palette = page.locator(".ct-v2-canvas-shell");
  await expect(flow).toBeVisible();

  await palette.getByTestId("workflow-palette-semantic_retrieve").dblclick();
  await palette.getByTestId("workflow-palette-memory_retrieve").dblclick();
  await expect.poll(() => canvas.locator(".react-flow__node-workflowNode").count()).toBe(6);
  await canvas.getByTitle("适应画布").click();

  const semantic = canvas.locator("[data-testid^='workflow-node-semantic_retrieve-']");
  const memory = canvas.locator("[data-testid^='workflow-node-memory_retrieve-']");
  await expect(semantic).toBeVisible();
  await expect(memory).toBeVisible();
  await semantic.click();
  const inspector = page.getByRole("complementary", { name: "节点属性" });
  await expect(inspector.getByLabel("节点 ID")).toHaveValue(/^semantic_retrieve-/);
  await expect(inspector.getByLabel("步骤 ID")).toBeVisible();
  await expect(inspector.getByRole("spinbutton", { name: "超时（秒）", exact: true })).toHaveValue("900");
  await inspector.getByLabel("失败策略").selectOption("continue_independent");
  await expect(inspector.getByLabel("失败策略")).toHaveValue("continue_independent");
  await inspector.getByRole("button", { name: "关闭属性面板" }).click();
  const semanticBox = await semantic.boundingBox();
  const memoryBox = await memory.boundingBox();
  expect(semanticBox).not.toBeNull();
  expect(memoryBox).not.toBeNull();
  if (!semanticBox || !memoryBox) return;

  const startX = Math.min(semanticBox.x, memoryBox.x) - 20;
  const startY = Math.min(semanticBox.y, memoryBox.y) - 20;
  const endX = Math.max(semanticBox.x + semanticBox.width, memoryBox.x + memoryBox.width) + 20;
  const endY = Math.max(semanticBox.y + semanticBox.height, memoryBox.y + memoryBox.height) + 20;
  await page.keyboard.down("Shift");
  await page.mouse.move(startX, startY);
  await page.mouse.down({ button: "left" });
  await page.mouse.move(endX, endY, { steps: 12 });
  await page.mouse.up({ button: "left" });
  await page.keyboard.up("Shift");

  await expect(canvas.getByTitle("删除所选")).toBeVisible();
  await canvas.getByTitle("删除所选").click();
  await expect.poll(() => canvas.locator(".react-flow__node-workflowNode").count()).toBe(4);
  await canvas.getByTitle("撤销").click();
  await expect.poll(() => canvas.locator(".react-flow__node-workflowNode").count()).toBe(5);
  await canvas.getByTitle("撤销").click();
  await expect.poll(() => canvas.locator(".react-flow__node-workflowNode").count()).toBe(6);
});

async function drag(page: import("@playwright/test").Page, source: import("@playwright/test").Locator, target: import("@playwright/test").Locator) {
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
