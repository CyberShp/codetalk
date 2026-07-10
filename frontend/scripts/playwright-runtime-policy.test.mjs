import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  acquirePublicRuntimeMutationLock,
  assertCanMutatePublicRuntime,
  isPublicLocalRuntime,
  resolveReuseExistingServer,
} from "./playwright-runtime-policy.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const e2eDir = path.resolve(__dirname, "../e2e");

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
  const lockDir = fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-public-runtime-lock-"));
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
        CODETALK_E2E_PUBLIC_RUNTIME_LOCK_DIR: lockDir,
        CODETALK_FRONTEND_PORT: "3003",
        CODETALK_BACKEND_PORT: "3004",
      },
      flowName: "SPDK real E2E",
    }),
  );
});

test("public runtime mutation guard prevents concurrent E2E processes from sharing cleanup state", () => {
  const lockDir = fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-public-runtime-lock-"));
  const env = {
    CODETALK_REUSE_EXISTING_SERVER: "1",
    CODETALK_E2E_ALLOW_PUBLIC_DATA_MUTATION: "1",
    CODETALK_E2E_PUBLIC_RUNTIME_LOCK_DIR: lockDir,
    CODETALK_FRONTEND_PORT: "3003",
    CODETALK_BACKEND_PORT: "3004",
  };

  const lockPath = acquirePublicRuntimeMutationLock({ env, flowName: "AI thread real E2E" });
  assert.equal(typeof lockPath, "string");
  assert.equal(acquirePublicRuntimeMutationLock({ env, flowName: "AI thread real E2E" }), lockPath);

  const secondLockDir = fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-public-runtime-lock-"));
  const secondLockPath = path.join(secondLockDir, "3003-3004.lock");
  fs.mkdirSync(secondLockPath, { recursive: true });
  fs.writeFileSync(path.join(secondLockPath, "owner.json"), JSON.stringify({ pid: 1 }), "utf8");
  assert.throws(
    () =>
      acquirePublicRuntimeMutationLock({
        env: { ...env, CODETALK_E2E_PUBLIC_RUNTIME_LOCK_DIR: secondLockDir },
        flowName: "AI thread real E2E",
      }),
    /refused to run concurrently/,
  );
});

test("E2E specs that mutate backend data declare the public-runtime mutation guard", () => {
  const violatingSpecs = fs
    .readdirSync(e2eDir)
    .filter((name) => name.endsWith(".spec.ts"))
    .filter((name) => {
      const source = fs.readFileSync(path.join(e2eDir, name), "utf8");
      const mutatesBackendData =
        (source.includes("/api/settings/agent-runtimes") && source.includes("request.post")) ||
        source.includes("request.post(`${backendBase}/api/tasks`") ||
        source.includes('pathname === "/api/coverage/upload"') ||
        (
          source.includes('page.goto("/workspaces/new"') &&
          source.includes('getByRole("button", { name: "创建工作空间" }).click()')
        );
      return (
        mutatesBackendData &&
        !source.includes("assertCanMutatePublicRuntime")
      );
    });

  assert.deepEqual(violatingSpecs, []);
});
