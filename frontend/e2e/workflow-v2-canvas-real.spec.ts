import { expect, test } from "@playwright/test";

import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

assertCanMutatePublicRuntime({
  env: process.env,
  flowName: "Workflow V2 canvas real E2E",
});

test("pans the workflow canvas by holding the left mouse button on the world background", async ({
  page,
  request,
}) => {
  const stamp = Date.now();
  const workflowId = `canvas_pan_e2e_${stamp}`;
  const workflowName = `Canvas Pan E2E ${stamp}`;
  const created = await request.post(`${backendBase}/api/workbench/workflows`, {
    data: {
      id: workflowId,
      name: workflowName,
      description: "真实鼠标画布平移回归",
      authoring_graph: {
        schema_version: 2,
        workflow_id: workflowId,
        name: workflowName,
        description: "真实鼠标画布平移回归",
        nodes: [
          {
            id: "target",
            kind: "input",
            label: "分析目标",
            position: { x: 80, y: 80 },
            config: {
              contract_id: "analysis_target",
              label: "分析目标",
              type: "text",
              required: true,
              resolver: "manual",
            },
          },
        ],
        edges: [],
        settings: { stop_on_error: true, max_parallelism: 1 },
      },
    },
  });
  expect(created.status()).toBe(201);

  try {
    await page.goto(`/workflows/${workflowId}`, { waitUntil: "domcontentloaded" });
    const stage = page.getByRole("region", { name: "工作流画布" });
    const board = stage.locator(".ct-v2-canvas-board");
    const world = stage.locator(".ct-v2-canvas-world");
    const node = stage.locator(".ct-v2-workflow-node");
    await expect(board).toBeVisible();

    const nodeBefore = await node.boundingBox();
    const handle = await node.locator(".ct-v2-node-drag").boundingBox();
    expect(nodeBefore).not.toBeNull();
    expect(handle).not.toBeNull();
    if (!nodeBefore || !handle) return;
    const worldBeforeNodeDrag = await world.getAttribute("style");
    await page.mouse.move(handle.x + 40, handle.y + 20);
    await page.mouse.down({ button: "left" });
    await page.mouse.move(handle.x + 140, handle.y + 80, { steps: 8 });
    await page.mouse.up({ button: "left" });
    const nodeAfter = await node.boundingBox();
    expect(nodeAfter).not.toBeNull();
    expect((nodeAfter?.x ?? 0) - nodeBefore.x).toBeGreaterThan(70);
    expect((nodeAfter?.y ?? 0) - nodeBefore.y).toBeGreaterThan(40);
    expect(await world.getAttribute("style")).toBe(worldBeforeNodeDrag);

    const cancelHandle = node.locator(".ct-v2-node-drag");
    const cancelHandleBox = await cancelHandle.boundingBox();
    expect(cancelHandleBox).not.toBeNull();
    if (!cancelHandleBox) return;
    await cancelHandle.evaluate((element) => {
      const handle = element as HTMLElement;
      handle.addEventListener(
        "pointerdown",
        (event) => {
          handle.dataset.testPointerId = String(event.pointerId);
        },
        { once: true },
      );
    });
    await page.mouse.move(cancelHandleBox.x + 40, cancelHandleBox.y + 20);
    await page.mouse.down({ button: "left" });
    await page.mouse.move(cancelHandleBox.x + 70, cancelHandleBox.y + 40, { steps: 3 });
    const pointerId = Number(await cancelHandle.getAttribute("data-test-pointer-id"));
    expect(pointerId).toBeGreaterThan(0);
    const nodeAtCancel = await node.boundingBox();
    expect(nodeAtCancel).not.toBeNull();
    await cancelHandle.dispatchEvent("pointercancel", {
      bubbles: true,
      button: 0,
      buttons: 0,
      clientX: cancelHandleBox.x + 70,
      clientY: cancelHandleBox.y + 40,
      isPrimary: true,
      pointerId,
      pointerType: "mouse",
    });
    await page.mouse.move(cancelHandleBox.x + 170, cancelHandleBox.y + 100, { steps: 5 });
    await page.mouse.up({ button: "left" });
    const nodeAfterCancel = await node.boundingBox();
    expect(nodeAfterCancel).not.toBeNull();
    expect(nodeAfterCancel?.x).toBeCloseTo(nodeAtCancel?.x ?? 0, 0);
    expect(nodeAfterCancel?.y).toBeCloseTo(nodeAtCancel?.y ?? 0, 0);

    const boardBox = await board.boundingBox();
    expect(boardBox).not.toBeNull();
    if (!boardBox) return;
    const start = {
      x: boardBox.x + boardBox.width - 90,
      y: boardBox.y + boardBox.height - 90,
    };
    const target = await page.evaluate(
      ({ x, y }) => {
        const element = document.elementFromPoint(x, y);
        return {
          insideWorld: Boolean(element?.closest(".ct-v2-canvas-world")),
          insideNode: Boolean(element?.closest(".ct-v2-workflow-node")),
        };
      },
      start,
    );
    expect(target).toEqual({ insideWorld: true, insideNode: false });

    const before = await world.getAttribute("style");
    await page.mouse.move(start.x, start.y);
    await page.mouse.down({ button: "left" });
    await page.mouse.move(start.x - 140, start.y - 70, { steps: 8 });
    await page.mouse.up({ button: "left" });

    await expect.poll(() => world.getAttribute("style")).not.toBe(before);
  } finally {
    await request.post(`${backendBase}/api/workbench/workflows/${workflowId}/archive`);
  }
});
