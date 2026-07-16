import assert from "node:assert/strict";
import test from "node:test";

import { workflowStepMcpProfiles } from "../src/features/tasks/task-wizard-contract.mjs";

test("normalizes legacy mcp_profile into the task wizard MCP list", () => {
  assert.deepEqual(
    workflowStepMcpProfiles({ mcp_profile: "codehub-mcp" }),
    ["codehub-mcp"],
  );
});

test("prefers, trims, and deduplicates mcp_profiles arrays", () => {
  assert.deepEqual(
    workflowStepMcpProfiles({
      mcp_profile: "legacy",
      mcp_profiles: [" gitnexus ", "cgc", "gitnexus", ""],
    }),
    ["gitnexus", "cgc"],
  );
});
