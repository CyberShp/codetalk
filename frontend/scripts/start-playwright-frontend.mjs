import { spawn } from "node:child_process";
import { assertPortAvailable } from "./port-preflight.mjs";

const frontendHost = process.env.CODETALK_FRONTEND_BIND_HOST ?? "0.0.0.0";
const frontendPort = process.env.CODETALK_FRONTEND_PORT ?? "3005";
const browserHost = process.env.CODETALK_BROWSER_HOST ?? "localhost";
const backendPort = process.env.CODETALK_BACKEND_PORT ?? "8100";
const nextPublicApiUrl =
  process.env.NEXT_PUBLIC_API_URL ?? `http://${browserHost}:${backendPort}`;
const npxCommand = process.platform === "win32" ? "npx.cmd" : "npx";

await assertPortAvailable({
  host: frontendHost,
  port: frontendPort,
  envName: "CODETALK_FRONTEND_PORT",
  serviceName: "CodeTalk frontend",
  clientHost: browserHost,
});

const child = spawn(npxCommand, ["next", "dev", "-H", frontendHost, "-p", frontendPort], {
  env: {
    ...process.env,
    NEXT_PUBLIC_API_URL: nextPublicApiUrl,
  },
  stdio: "inherit",
});

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
    const signalExitCodes = { SIGINT: 130, SIGTERM: 143, SIGKILL: 137 };
    process.exit(signalExitCodes[signal] ?? 1);
    return;
  }
  process.exit(code ?? 0);
});
