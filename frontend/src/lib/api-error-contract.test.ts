import assert from "node:assert/strict";
import test from "node:test";

import { ApiRequestError, request } from "./api.ts";

test("failed derived workflow action exposes the preserved draft revision", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(
    JSON.stringify({
      detail: {
        message: "工作流存在阻断问题",
        errors: [{ code: "required_input_unbound" }],
        draft_revision: 17,
      },
    }),
    { status: 422, headers: { "content-type": "application/json" } },
  );

  try {
    await assert.rejects(
      () => request("/api/workbench/workflows/wf/versions/v/compile", { method: "POST" }),
      (cause: unknown) => {
        assert.ok(cause instanceof ApiRequestError);
        assert.equal((cause as ApiRequestError & { draftRevision?: number }).draftRevision, 17);
        return true;
      },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("structured upload conflict exposes the stable stale draft code", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(
    JSON.stringify({
      detail: {
        code: "stale_draft",
        message: "画布已被其他窗口更新。请刷新后重新选择文件。",
        draft_revision: 18,
      },
    }),
    { status: 409, headers: { "content-type": "application/json" } },
  );

  try {
    await assert.rejects(
      () => request("/api/workbench/input-files/upload", { method: "POST" }),
      (cause: unknown) => {
        assert.ok(cause instanceof ApiRequestError);
        assert.equal(cause.status, 409);
        assert.equal(cause.errorCode, "stale_draft");
        assert.equal(cause.draftRevision, 18);
        return true;
      },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
