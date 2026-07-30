import test from "node:test";
import assert from "node:assert/strict";

import {
  hasV3RunAxisSummary,
  isV3TaskRun,
  taskRunOverviewProjection,
} from "../src/features/tasks/task-run-status-projection.mjs";

const v3Task = {
  workflow_version: {
    compiled_definition: {
      compiled_contract_version: 3,
    },
  },
};

test("V3 task overview hydrates latest run axes from run detail when summary is thin", () => {
  const latestRun = {
    task_run_id: "task_run_1",
    execution_status: "completed",
    quality_status: "blocked",
    delivery_status: "blocked",
  };
  const runDetail = {
    task_run_id: "task_run_1",
    execution_status: "completed",
    artifact_validation_status: "failed",
    governance_status: "failed",
    delivery_status: "blocked",
    workflow_snapshot: { compiled_contract_version: 3 },
    task_bundle: { compiled_contract_version: 3 },
  };

  assert.equal(isV3TaskRun(v3Task, latestRun), true);
  assert.equal(hasV3RunAxisSummary(latestRun), false);
  assert.deepEqual(
    taskRunOverviewProjection(v3Task, latestRun, runDetail),
    {
      kind: "v3",
      execution: "completed",
      artifactValidation: "failed",
      governance: "failed",
      delivery: "blocked",
    },
  );
});

test("V3 task overview shows syncing while the detailed run is still loading", () => {
  const latestRun = {
    task_run_id: "task_run_2",
    execution_status: "completed",
    quality_status: "blocked",
    delivery_status: "blocked",
  };

  assert.deepEqual(
    taskRunOverviewProjection(v3Task, latestRun, null, { loadingDetail: true }),
    {
      kind: "v3",
      execution: "completed",
      artifactValidation: "syncing",
      governance: "syncing",
      delivery: "blocked",
    },
  );
});

test("legacy task overview keeps the quality and delivery projection", () => {
  const latestRun = {
    task_run_id: "task_run_3",
    execution_status: "completed",
    quality_status: "passed",
    delivery_status: "complete",
  };

  assert.deepEqual(
    taskRunOverviewProjection({}, latestRun),
    {
      kind: "legacy",
      execution: "completed",
      quality: "passed",
      delivery: "complete",
    },
  );
});
