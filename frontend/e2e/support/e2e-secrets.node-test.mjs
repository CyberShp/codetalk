import { strict as assert } from "node:assert";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { readSecretValue } from "./e2e-secrets.mjs";

test("readSecretValue prefers the direct environment value", () => {
  assert.equal(
    readSecretValue("CODETALK_E2E_LLM_API_KEY", {
      CODETALK_E2E_LLM_API_KEY: "direct-secret",
      CODETALK_E2E_LLM_API_KEY_FILE: "/tmp/unused",
    }),
    "direct-secret",
  );
});

test("readSecretValue reads a trimmed secret from NAME_FILE", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-e2e-secret-"));
  const secretPath = path.join(dir, "llm-key");
  try {
    fs.writeFileSync(secretPath, "file-secret\n", { mode: 0o600 });
    assert.equal(
      readSecretValue("CODETALK_E2E_LLM_API_KEY", {
        CODETALK_E2E_LLM_API_KEY_FILE: secretPath,
      }),
      "file-secret",
    );
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
