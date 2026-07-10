import test from "node:test";
import assert from "node:assert/strict";

import { deriveRunPanelStatus } from "../src/lib/run-panel-status.mjs";

test("needs-rework summaries settle as review instead of failure or running", () => {
  assert.equal(
    deriveRunPanelStatus({
      hasPreparedRun: true,
      activeStatusLabel: "需要复核",
      testActivityStatus: "needs_rework",
      acceptanceStatus: "incomplete",
      missingRequired: 1,
      workflowStatus: "needs_rework",
    }),
    "需复核",
  );
});

test("hard execution failures remain failures", () => {
  assert.equal(
    deriveRunPanelStatus({
      hasPreparedRun: true,
      activeStatusLabel: "运行失败",
      workflowStatus: "failed",
    }),
    "失败",
  );
});
