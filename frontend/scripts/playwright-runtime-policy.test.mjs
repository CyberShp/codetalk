import test, { after } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  acquirePublicRuntimeMutationLock,
  assertCanMutatePublicRuntime,
  configureRuntimeTempEnvironment,
  isPublicLocalRuntime,
  resolveReuseExistingServer,
  sanitizePlaywrightRunId,
} from "./playwright-runtime-policy.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const e2eDir = path.resolve(__dirname, "../e2e");
const createdTempDirs = [];

function makeTestTemp(prefix) {
  const root = process.env.CODETALK_TEMP_DIR ?? os.tmpdir();
  fs.mkdirSync(root, { recursive: true });
  const tempDir = fs.mkdtempSync(path.join(root, prefix));
  createdTempDirs.push(tempDir);
  return tempDir;
}

after(() => {
  for (const tempDir of createdTempDirs) {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

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
  const lockDir = makeTestTemp("codetalk-public-runtime-lock-");
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
  const lockDir = makeTestTemp("codetalk-public-runtime-lock-");
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

  const secondLockDir = makeTestTemp("codetalk-public-runtime-lock-");
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

test("public runtime lock follows CODETALK_TEMP_DIR by default", () => {
  const tempRoot = makeTestTemp("codetalk-configured-temp-");
  const lockPath = acquirePublicRuntimeMutationLock({
    env: {
      CODETALK_REUSE_EXISTING_SERVER: "1",
      CODETALK_E2E_ALLOW_PUBLIC_DATA_MUTATION: "1",
      CODETALK_TEMP_DIR: tempRoot,
      CODETALK_FRONTEND_PORT: "3003",
      CODETALK_BACKEND_PORT: "3004",
      CODETALK_PLAYWRIGHT_RUN_ID: `temp-root-${process.pid}`,
    },
    flowName: "configured temp root",
  });

  assert.equal(lockPath, path.join(tempRoot, "codetalk-e2e-public-runtime-locks", "3003-3004.lock"));
  assert.equal(fs.existsSync(lockPath), true);
});

test("configured temp root also drives Node and browser child temporary variables", () => {
  const tempRoot = makeTestTemp("codetalk-runtime-temp-");
  const env = { CODETALK_TEMP_DIR: tempRoot };

  assert.equal(configureRuntimeTempEnvironment(env), path.resolve(tempRoot));
  for (const name of ["TEMP", "TMP", "TMPDIR"]) {
    assert.equal(env[name], path.resolve(tempRoot));
  }
});

test("Playwright run ids cannot escape the configured temp root", () => {
  const runId = sanitizePlaywrightRunId("../../outside/run\\name");

  assert.equal(runId.includes(".."), false);
  assert.equal(runId.includes("/"), false);
  assert.equal(runId.includes("\\"), false);
  assert.match(runId, /^[A-Za-z0-9_-]+$/);
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
