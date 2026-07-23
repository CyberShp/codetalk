import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const PUBLIC_FRONTEND_PORT = "3003";
const PUBLIC_BACKEND_PORT = "3004";
const heldPublicRuntimeLocks = new Set();

export function configureRuntimeTempEnvironment(env = process.env) {
  const configuredTempRoot = env.CODETALK_TEMP_DIR ?? env.CODETALK_RUNTIME_TEMP_ROOT;
  const runtimeTempRoot = path.resolve(configuredTempRoot ?? os.tmpdir());
  if (configuredTempRoot) {
    // Keep every Node, browser, and backend child on the declared runtime volume.
    env.CODETALK_TEMP_DIR = runtimeTempRoot;
    for (const name of ["TEMP", "TMP", "TMPDIR"]) env[name] = runtimeTempRoot;
  }
  return runtimeTempRoot;
}

export function sanitizePlaywrightRunId(value, fallback = `run-${process.pid}`) {
  const sanitized = String(value ?? "")
    .trim()
    .replace(/[^A-Za-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 96);
  return sanitized || fallback;
}

export function resolveReuseExistingServer(env = process.env) {
  return env.CODETALK_REUSE_EXISTING_SERVER === "1";
}

export function isPublicLocalRuntime({ frontendPort, backendPort }) {
  return String(frontendPort ?? "") === PUBLIC_FRONTEND_PORT && String(backendPort ?? "") === PUBLIC_BACKEND_PORT;
}

export function assertCanMutatePublicRuntime({
  env = process.env,
  flowName = "Playwright E2E",
  frontendPort = env.CODETALK_FRONTEND_PORT ?? PUBLIC_FRONTEND_PORT,
  backendPort = env.CODETALK_BACKEND_PORT ?? PUBLIC_BACKEND_PORT,
} = {}) {
  if (!resolveReuseExistingServer(env)) return;
  if (!isPublicLocalRuntime({ frontendPort, backendPort })) return;
  if (env.CODETALK_E2E_ALLOW_PUBLIC_DATA_MUTATION === "1") {
    acquirePublicRuntimeMutationLock({ env, flowName, frontendPort, backendPort });
    return;
  }

  throw new Error(
    [
      `${flowName} refused to mutate the public local CodeTalk runtime (${PUBLIC_FRONTEND_PORT}/${PUBLIC_BACKEND_PORT}).`,
      "Use isolated Playwright servers, or set CODETALK_E2E_ALLOW_PUBLIC_DATA_MUTATION=1 when intentionally validating the live local runtime.",
    ].join(" "),
  );
}

export function acquirePublicRuntimeMutationLock({
  env = process.env,
  flowName = "Playwright E2E",
  frontendPort = env.CODETALK_FRONTEND_PORT ?? PUBLIC_FRONTEND_PORT,
  backendPort = env.CODETALK_BACKEND_PORT ?? PUBLIC_BACKEND_PORT,
} = {}) {
  if (!resolveReuseExistingServer(env)) return null;
  if (!isPublicLocalRuntime({ frontendPort, backendPort })) return null;
  if (env.CODETALK_E2E_ALLOW_PUBLIC_DATA_MUTATION !== "1") return null;

  const runtimeTempRoot = env.CODETALK_TEMP_DIR ?? env.CODETALK_RUNTIME_TEMP_ROOT ?? os.tmpdir();
  const lockRoot = env.CODETALK_E2E_PUBLIC_RUNTIME_LOCK_DIR ?? path.join(runtimeTempRoot, "codetalk-e2e-public-runtime-locks");
  const lockPath = path.join(lockRoot, `${frontendPort}-${backendPort}.lock`);
  if (heldPublicRuntimeLocks.has(lockPath)) return lockPath;

  fs.mkdirSync(lockRoot, { recursive: true });
  const ownsLock = tryAcquireLock(lockPath, { env, flowName, frontendPort, backendPort });
  if (!ownsLock) return lockPath;

  heldPublicRuntimeLocks.add(lockPath);

  const release = () => {
    if (!heldPublicRuntimeLocks.delete(lockPath)) return;
    try {
      const owner = readLockOwner(lockPath);
      if (owner.pid === process.pid) fs.rmSync(lockPath, { recursive: true, force: true });
    } catch {
      /* best-effort cleanup */
    }
  };
  process.once("exit", release);
  process.once("SIGINT", () => {
    release();
    process.exit(130);
  });
  process.once("SIGTERM", () => {
    release();
    process.exit(143);
  });
  return lockPath;
}

function tryAcquireLock(lockPath, { env, flowName, frontendPort, backendPort }) {
  try {
    fs.mkdirSync(lockPath);
    writeLockOwner(lockPath, { env, flowName, frontendPort, backendPort });
    return true;
  } catch (error) {
    if (error?.code !== "EEXIST") throw error;
  }

  const owner = readLockOwner(lockPath);
  const runId = String(env.CODETALK_PLAYWRIGHT_RUN_ID ?? "");
  if (runId && owner.runId === runId) return false;
  if (owner.pid === process.pid) {
    writeLockOwner(lockPath, { env, flowName, frontendPort, backendPort });
    return true;
  }
  if (owner.pid && !processIsAlive(owner.pid)) {
    fs.rmSync(lockPath, { recursive: true, force: true });
    fs.mkdirSync(lockPath);
    writeLockOwner(lockPath, { env, flowName, frontendPort, backendPort });
    return true;
  }
  throw new Error(
    [
      `${flowName} refused to run concurrently against the public local CodeTalk runtime (${frontendPort}/${backendPort}).`,
      `Another E2E process owns ${lockPath}${owner.pid ? ` (pid ${owner.pid})` : ""}${owner.runId ? ` run ${owner.runId}` : ""}.`,
      "Run public-runtime mutation E2E sequentially, or use isolated Playwright servers.",
    ].join(" "),
  );
}

function writeLockOwner(lockPath, { env, flowName, frontendPort, backendPort }) {
  fs.writeFileSync(
    path.join(lockPath, "owner.json"),
    JSON.stringify(
      {
        pid: process.pid,
        runId: String(env.CODETALK_PLAYWRIGHT_RUN_ID ?? ""),
        flowName,
        frontendPort: String(frontendPort),
        backendPort: String(backendPort),
        startedAt: new Date().toISOString(),
      },
      null,
      2,
    ),
    "utf8",
  );
}

function readLockOwner(lockPath) {
  try {
    const owner = JSON.parse(fs.readFileSync(path.join(lockPath, "owner.json"), "utf8"));
    return { pid: Number(owner.pid) || 0, runId: String(owner.runId ?? "") };
  } catch {
    return { pid: 0, runId: "" };
  }
}

function processIsAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error?.code === "EPERM";
  }
}
