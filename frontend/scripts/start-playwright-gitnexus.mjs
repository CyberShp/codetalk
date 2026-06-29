import { spawn } from "node:child_process";

const gitnexusHost = process.env.CODETALK_GITNEXUS_BIND_HOST ?? "0.0.0.0";
const gitnexusPort = process.env.GITNEXUS_PORT ?? process.env.CODETALK_GITNEXUS_PORT ?? "7100";
const gitnexusCommand = process.env.GITNEXUS_BIN ?? "gitnexus";

const child = spawn(
  gitnexusCommand,
  ["serve", "--host", gitnexusHost, "--port", gitnexusPort],
  {
    env: {
      ...process.env,
      GITNEXUS_PORT: gitnexusPort,
    },
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
    const signalExitCodes = { SIGINT: 130, SIGTERM: 143, SIGKILL: 137 };
    process.exit(signalExitCodes[signal] ?? 1);
    return;
  }
  process.exit(code ?? 0);
});
