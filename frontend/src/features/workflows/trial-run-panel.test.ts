import assert from "node:assert/strict";
import test from "node:test";

import { prepareTrialRunWithUploads } from "./trial-run-contract.ts";


const file = new File(["# Design\n"], "design.md", { type: "text/markdown" });


test("trial preparation obtains the saved revision before uploading files", async () => {
  const calls: string[] = [];

  const prepared = await prepareTrialRunWithUploads({
    values: { analysis_target: "NVMe/TCP TLS" },
    files: [{ inputId: "design_doc", file }],
    beforeRun: async () => { calls.push("save"); return 7; },
    uploadInput: async (_file, _inputId, lease) => {
      assert.ok(lease);
      calls.push(`upload:${lease.expectedRevision}`);
      return {
        upload_id: "input_123",
        cleanup_token: "cleanup-secret",
        input_payload: { path: "input_uploads/input_123/design.md" },
      };
    },
    prepareRun: async (inputs, expectedRevision) => {
      calls.push(`prepare:${expectedRevision}`);
      assert.deepEqual(inputs.design_doc, { path: "input_uploads/input_123/design.md" });
      return { task_run_id: "task_123" };
    },
    releaseUpload: async () => { calls.push("release"); },
    workflowId: "workflow_123",
    workflowVersionId: "wfv_123",
  });

  assert.equal(prepared.task_run_id, "task_123");
  assert.deepEqual(calls, ["save", "upload:7", "prepare:7"]);
});


test("second file stale response releases every previously uploaded lease", async () => {
  const released: string[] = [];
  const uploadCalls: string[] = [];

  await assert.rejects(
    prepareTrialRunWithUploads({
      values: {},
      files: [
        { inputId: "design_doc", file },
        { inputId: "coverage", file },
      ],
      beforeRun: async () => 11,
      uploadInput: async (_file, inputId) => {
        uploadCalls.push(inputId);
        if (inputId === "coverage") {
          throw Object.assign(new Error("stale_draft"), {
            status: 409,
            errorCode: "stale_draft",
          });
        }
        return {
          upload_id: `input_${inputId}`,
          cleanup_token: `token_${inputId}`,
          input_payload: { path: `input_uploads/input_${inputId}/design.md` },
        };
      },
      prepareRun: async () => {
        throw Object.assign(new Error("prepare must not run"), {
          status: 409,
        });
      },
      releaseUpload: async (uploadId, cleanupToken) => {
        released.push(`${uploadId}:${cleanupToken}`);
      },
      workflowId: "workflow_123",
      workflowVersionId: "wfv_123",
    }),
    /stale_draft/,
  );

  assert.deepEqual(uploadCalls, ["design_doc", "coverage"]);
  assert.deepEqual(released, ["input_design_doc:token_design_doc"]);
});


test("prepare stale response releases all successfully uploaded leases", async () => {
  const released: string[] = [];

  await assert.rejects(
    prepareTrialRunWithUploads({
      values: {},
      files: [
        { inputId: "design_doc", file },
        { inputId: "coverage", file },
      ],
      beforeRun: async () => 11,
      uploadInput: async (_file, inputId) => ({
        upload_id: `input_${inputId}`,
        cleanup_token: `token_${inputId}`,
        input_payload: { path: `input_uploads/input_${inputId}/design.md` },
      }),
      prepareRun: async () => {
        throw Object.assign(new Error("stale_draft"), {
          status: 409,
          errorCode: "stale_draft",
        });
      },
      releaseUpload: async (uploadId, cleanupToken) => {
        released.push(`${uploadId}:${cleanupToken}`);
      },
      workflowId: "workflow_123",
      workflowVersionId: "wfv_123",
    }),
    /stale_draft/,
  );

  assert.deepEqual(released.sort(), [
    "input_coverage:token_coverage",
    "input_design_doc:token_design_doc",
  ]);
});


test("unknown result from the second upload retains every successful lease", async () => {
  let releaseCount = 0;

  await assert.rejects(
    prepareTrialRunWithUploads({
      values: {},
      files: [
        { inputId: "design_doc", file },
        { inputId: "coverage", file },
      ],
      beforeRun: async () => 11,
      uploadInput: async (_file, inputId) => {
        if (inputId === "coverage") throw new Error("network interrupted");
        return {
          upload_id: "input_unknown",
          cleanup_token: "token_unknown",
          input_payload: { path: "input_uploads/input_unknown/design.md" },
        };
      },
      prepareRun: async () => { throw new Error("prepare must not run"); },
      releaseUpload: async () => { releaseCount += 1; },
      workflowId: "workflow_123",
      workflowVersionId: "wfv_123",
    }),
    /network interrupted/,
  );

  assert.equal(releaseCount, 0);
});


test("successful trial preparation retains uploads for the task snapshot", async () => {
  let releaseCount = 0;

  await prepareTrialRunWithUploads({
    values: {},
    files: [{ inputId: "design_doc", file }],
    beforeRun: async () => 3,
    uploadInput: async () => ({
      upload_id: "input_kept",
      cleanup_token: "token_kept",
      input_payload: { path: "input_uploads/input_kept/design.md" },
    }),
    prepareRun: async () => ({ task_run_id: "task_kept" }),
    releaseUpload: async () => { releaseCount += 1; },
    workflowId: "workflow_123",
    workflowVersionId: "wfv_123",
  });

  assert.equal(releaseCount, 0);
});
