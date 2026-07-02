import test from "node:test";
import assert from "node:assert/strict";
import {
  assertCanMutatePublicRuntime,
  isPublicLocalRuntime,
  resolveReuseExistingServer,
} from "./playwright-runtime-policy.mjs";

test("Playwright does not reuse an existing backend unless explicitly requested", () => {
  assert.equal(resolveReuseExistingServer({}), false);
  assert.equal(resolveReuseExistingServer({ CODETALK_REUSE_EXISTING_SERVER: "0" }), false);
  assert.equal(resolveReuseExistingServer({ CODETALK_REUSE_EXISTING_SERVER: "1" }), true);
});

test("3003/3004 is recognized as the public local runtime", () => {
  assert.equal(isPublicLocalRuntime({ frontendPort: "3003", backendPort: "3004" }), true);
  assert.equal(isPublicLocalRuntime({ frontendPort: "3103", backendPort: "3104" }), false);
});

test("mutating SPDK E2E refuses to reuse the public runtime without an explicit opt-in", () => {
  assert.throws(
    () =>
      assertCanMutatePublicRuntime({
        env: {
          CODETALK_REUSE_EXISTING_SERVER: "1",
          CODETALK_FRONTEND_PORT: "3003",
          CODETALK_BACKEND_PORT: "3004",
        },
        flowName: "SPDK real E2E",
      }),
    /CODETALK_E2E_ALLOW_PUBLIC_DATA_MUTATION=1/,
  );

  assert.doesNotThrow(() =>
    assertCanMutatePublicRuntime({
      env: {
        CODETALK_REUSE_EXISTING_SERVER: "1",
        CODETALK_E2E_ALLOW_PUBLIC_DATA_MUTATION: "1",
        CODETALK_FRONTEND_PORT: "3003",
        CODETALK_BACKEND_PORT: "3004",
      },
      flowName: "SPDK real E2E",
    }),
  );
});
