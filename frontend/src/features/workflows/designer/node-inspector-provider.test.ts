import assert from "node:assert/strict";
import test from "node:test";

import {
  providerConfigValue,
  providerSelectionPatch,
} from "./provider-contract.ts";

test("V3 provider selection stores provider_ref as the canonical contract", () => {
  for (const provider of ["builtin-llm", "codex", "claude-code", "opencode"]) {
    assert.deepEqual(providerSelectionPatch(provider, true), {
      provider_ref: provider,
      provider: undefined,
      mcp_profiles: [],
    });
  }
  assert.equal(providerConfigValue({ provider_ref: "claude-code" }), "claude-code");
});

test("provider selector reads legacy provider and keeps V2 writes compatible", () => {
  assert.equal(providerConfigValue({ provider: "opencode" }), "opencode");
  assert.deepEqual(providerSelectionPatch("builtin-llm", false), {
    provider: "builtin-llm",
    mcp_profiles: [],
  });
});
