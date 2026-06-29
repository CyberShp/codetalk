import { spawn, spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const backendDir = path.resolve(__dirname, "../../backend");
const backendHost = process.env.CODETALK_BACKEND_BIND_HOST ?? "0.0.0.0";
const backendPort = process.env.CODETALK_BACKEND_PORT ?? "8100";
const configuredPython = process.env.CODETALK_BACKEND_PYTHON;
const candidates = configuredPython
  ? [configuredPython]
  : ["python3.11", "python3.10", "python3", "python"];

function isSupportedPython(command) {
  const result = spawnSync(
    command,
    [
      "-c",
      [
        "import sys",
        "import uvicorn, fastapi, pydantic_settings",
        "raise SystemExit(0 if sys.version_info >= (3, 10) else 1)",
      ].join("; "),
    ],
    { stdio: "ignore" },
  );
  return result.status === 0;
}

const python = candidates.find(isSupportedPython);
if (!python) {
  console.error(
    "No Python >=3.10 interpreter with CodeTalk backend dependencies found. Set CODETALK_BACKEND_PYTHON.",
  );
  process.exit(1);
}

const child = spawn(
  python,
  ["-m", "uvicorn", "app.main:app", "--host", backendHost, "--port", backendPort],
  {
    cwd: backendDir,
    env: process.env,
    stdio: "inherit",
  },
);

function shutdown(signal) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.kill(signal);
  const forceKill = setTimeout(() => {
    if (child.exitCode === null && child.signalCode === null) {
      child.kill("SIGKILL");
    }
  }, 5000);
  forceKill.unref();
  child.once("exit", () => clearTimeout(forceKill));
}

process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 0);
});
