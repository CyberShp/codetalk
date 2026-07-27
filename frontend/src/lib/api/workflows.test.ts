import assert from "node:assert/strict";
import test from "node:test";

import { workflowRevisionBody } from "./workflow-action-contract.ts";

test("derived V3 workflow actions send the caller draft revision", async () => {
  assert.deepEqual(JSON.parse(workflowRevisionBody(7)), { expected_revision: 7 });
  assert.deepEqual(JSON.parse(workflowRevisionBody(8)), { expected_revision: 8 });
  assert.deepEqual(JSON.parse(workflowRevisionBody(9)), { expected_revision: 9 });
  assert.deepEqual(JSON.parse(workflowRevisionBody()), {});
});
