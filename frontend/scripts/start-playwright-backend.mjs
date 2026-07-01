import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { assertPortAvailable } from "./port-preflight.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const backendDir = path.resolve(__dirname, "../../backend");
const backendHost = process.env.CODETALK_BACKEND_BIND_HOST ?? "0.0.0.0";
const backendPort = process.env.CODETALK_BACKEND_PORT ?? "3004";
const frontendPort = process.env.CODETALK_FRONTEND_PORT ?? "3003";
const browserHost = process.env.CODETALK_BROWSER_HOST ?? "localhost";
const gitnexusPort = process.env.GITNEXUS_PORT ?? process.env.CODETALK_GITNEXUS_PORT ?? "7100";
const gitnexusBaseUrl =
  process.env.GITNEXUS_BASE_URL ?? `http://localhost:${gitnexusPort}`;
const configuredPython = process.env.CODETALK_BACKEND_PYTHON;
const runId =
  process.env.CODETALK_PLAYWRIGHT_RUN_ID ??
  `${new Date().toISOString().replace(/[:.]/g, "-")}-${process.pid}`;
const isolatedDataDir =
  process.env.CODETALK_PLAYWRIGHT_DATA_DIR ??
  path.join(os.tmpdir(), "codetalk-playwright", `backend-${backendPort}`, runId);
const isolatedSqliteDb =
  process.env.CODETALK_PLAYWRIGHT_SQLITE_DB ?? path.join(isolatedDataDir, "codetalk.db");
const shouldCleanupDataDir =
  process.env.CODETALK_PLAYWRIGHT_KEEP_DATA !== "1" &&
  !process.env.CODETALK_PLAYWRIGHT_DATA_DIR &&
  !process.env.CODETALK_PLAYWRIGHT_SQLITE_DB;
const candidates = configuredPython
  ? [configuredPython]
  : ["python3.11", "python3.10", "python3", "python"];
const corsOrigins =
  process.env.CORS_ORIGINS ??
  [
    `http://${browserHost}:${frontendPort}`,
    `http://localhost:${frontendPort}`,
    `http://127.0.0.1:${frontendPort}`,
  ].join(",");

await assertPortAvailable({
  host: backendHost,
  port: backendPort,
  envName: "CODETALK_BACKEND_PORT",
  serviceName: "CodeTalk backend",
  clientHost: browserHost,
});

function isSupportedPython(command) {
  const result = spawnSync(
    command,
    [
      "-c",
      [
        "import sys",
        "assert sys.version_info >= (3, 10)",
        "import uvicorn",
        "import app.main",
      ].join("; "),
    ],
    {
      cwd: backendDir,
      env: {
        ...process.env,
        DATA_DIR: isolatedDataDir,
        SQLITE_DB: isolatedSqliteDb,
        CORS_ORIGINS: corsOrigins,
        GITNEXUS_BASE_URL: gitnexusBaseUrl,
        GITNEXUS_PORT: gitnexusPort,
      },
      stdio: "ignore",
    },
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

fs.mkdirSync(isolatedDataDir, { recursive: true });
fs.mkdirSync(path.dirname(isolatedSqliteDb), { recursive: true });

function buildExternalAgentProviders(existing, slowAgentCommand) {
  const slowAgent = {
    id: "slow-agent",
    command: slowAgentCommand,
    prompt_transport: "stdin",
    supports_artifact_export: true,
    supports_json_output: true,
  };
  const raw = String(existing ?? "").trim();
  if (!raw) return JSON.stringify([slowAgent]);
  try {
    const parsed = JSON.parse(raw);
    const providers = Array.isArray(parsed) ? parsed : [parsed];
    if (providers.some((item) => item && typeof item === "object" && item.id === "slow-agent")) {
      return raw;
    }
    return JSON.stringify([...providers, slowAgent]);
  } catch {
    return JSON.stringify([
      { id: "external-agent", command: raw, prompt_transport: "stdin" },
      slowAgent,
    ]);
  }
}

const slowAgentScript = path.join(isolatedDataDir, "playwright-slow-agent.py");
fs.writeFileSync(
  slowAgentScript,
  [
    "import json",
    "import os",
    "import pathlib",
    "import sys",
    "import time",
    "",
    "prompt = sys.stdin.read()",
    "time.sleep(float(os.environ.get('CODETALK_E2E_SLOW_AGENT_DELAY', '2.0')))",
    "artifact_dir = pathlib.Path(os.environ.get('CODETALK_AGENT_ARTIFACT_DIR', '.'))",
    "artifact_dir.mkdir(parents=True, exist_ok=True)",
    "payload = {'status': 'ok', 'provider': 'slow-agent', 'prompt_chars': len(prompt)}",
    "(artifact_dir / 'result.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')",
    "print(json.dumps({'status': 'ok', 'summary': 'slow-agent completed', 'candidate_files': [], 'warnings': []}, ensure_ascii=False))",
    "",
  ].join("\n"),
  "utf8",
);

const externalAgentCustomProviders = buildExternalAgentProviders(
  process.env.EXTERNAL_AGENT_CUSTOM_PROVIDERS,
  `${python} ${slowAgentScript}`,
);
const backendEnv = {
  ...process.env,
  DATA_DIR: isolatedDataDir,
  SQLITE_DB: isolatedSqliteDb,
  CORS_ORIGINS: corsOrigins,
  GITNEXUS_BASE_URL: gitnexusBaseUrl,
  GITNEXUS_PORT: gitnexusPort,
  EXTERNAL_AGENT_CUSTOM_PROVIDERS: externalAgentCustomProviders,
};

let cleanedDataDir = false;
function cleanupDataDir() {
  if (!shouldCleanupDataDir || cleanedDataDir) return;
  cleanedDataDir = true;
  try {
    fs.rmSync(isolatedDataDir, { recursive: true, force: true });
  } catch {
    // Best-effort cleanup; per-run isolation still prevents reuse of secrets.
  }
}

const child = spawn(
  python,
  ["-m", "uvicorn", "app.main:app", "--host", backendHost, "--port", backendPort],
  {
    cwd: backendDir,
    env: backendEnv,
    stdio: "inherit",
  },
);

function shutdown(signal) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.kill(signal);
  cleanupDataDir();
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
process.on("exit", cleanupDataDir);

child.on("exit", (code, signal) => {
  cleanupDataDir();
  if (signal) {
    const signalExitCodes = { SIGINT: 130, SIGTERM: 143, SIGKILL: 137 };
    process.exit(signalExitCodes[signal] ?? 1);
    return;
  }
  process.exit(code ?? 0);
});
