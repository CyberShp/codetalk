import assert from "node:assert/strict";
import test from "node:test";

import {
  formatStageAttemptLabel,
  selectStageAttemptStart,
  selectStageProgressEvent,
} from "./stage-progress-event.ts";

test("partial runs keep the latest partial stage visible", () => {
  const timedOut = {
    payload: { kind: "stage_timed_out", stage_id: "business_flow", status: "partial" },
  };
  const laterCompleted = {
    payload: { kind: "stage_completed", stage_id: "evidence_cards", status: "completed" },
  };

  assert.equal(selectStageProgressEvent([timedOut, laterCompleted], true), timedOut);
  assert.equal(selectStageProgressEvent([timedOut, laterCompleted], false), laterCompleted);
});

test("repaired stages measure elapsed time from the latest provider attempt", () => {
  const originalStart = {
    payload: { kind: "stage_provider_started", stage_id: "sfmea", attempt_count: 1 },
  };
  const originalCompleted = {
    payload: { kind: "stage_completed", stage_id: "sfmea", status: "completed" },
  };
  const repairStart = {
    payload: { kind: "stage_provider_started", stage_id: "sfmea", attempt_count: 1 },
  };
  const repairOutput = {
    payload: { kind: "stage_output_delta", stage_id: "sfmea", status: "running" },
  };

  assert.equal(
    selectStageAttemptStart(
      [originalStart, originalCompleted, repairStart, repairOutput],
      "sfmea",
    ),
    repairStart,
  );
});

test("independent claim validation is shown as an active model audit", () => {
  assert.equal(
    formatStageAttemptLabel({
      stage_id: "behavior_claim_validation",
      kind: "stage_provider_started",
      status: "running",
      attempt_count: 0,
    }),
    "正在进行事实核验",
  );
});

test("completed independent claim validation is not mislabeled as no model call", () => {
  assert.equal(
    formatStageAttemptLabel({
      stage_id: "behavior_claim_validation",
      kind: "stage_completed",
      status: "completed",
      attempt_count: 0,
    }),
    "事实核验已完成",
  );
});
