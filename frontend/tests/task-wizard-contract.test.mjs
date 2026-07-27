import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { workflowStepMcpProfiles } from "../src/features/tasks/task-wizard-contract.mjs";

const root = dirname(fileURLToPath(import.meta.url));
const taskWizardSource = readFileSync(
  join(root, "../src/features/tasks/task-wizard.tsx"),
  "utf8",
);

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

test("file upload merges into the latest input state instead of an async render snapshot", () => {
  assert.match(
    taskWizardSource,
    /const uploaded = await Promise\.all[\s\S]{0,400}onChange\(\(currentValues\) => \(\{ \.\.\.currentValues, \[id\]:/,
  );
  assert.doesNotMatch(
    taskWizardSource,
    /const uploaded = await Promise\.all[\s\S]{0,400}onChange\(\{ \.\.\.values, \[id\]:/,
  );
});
