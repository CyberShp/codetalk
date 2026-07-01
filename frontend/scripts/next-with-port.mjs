import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { clearNextDevCache } from "./next-dev-cache.mjs";
import { assertPortAvailable } from "./port-preflight.mjs";

const mode = process.argv[2];
if (!["dev", "start"].includes(mode)) {
  console.error("Usage: node scripts/next-with-port.mjs <dev|start>");
  process.exit(1);
}

const port = process.env.CODETALK_FRONTEND_PORT ?? process.env.PORT ?? "3003";
const host = process.env.CODETALK_FRONTEND_BIND_HOST ?? "0.0.0.0";
const browserHost = process.env.CODETALK_BROWSER_HOST ?? "localhost";
const require = createRequire(import.meta.url);
const nextBin = require.resolve("next/dist/bin/next");

await assertPortAvailable({
  host,
  port,
  envName: "CODETALK_FRONTEND_PORT",
  serviceName: "CodeTalk frontend",
  clientHost: browserHost,
});

if (mode === "dev") {
  await clearNextDevCache({ reason: "before npm run dev" });
}

const child = spawn(process.execPath, [nextBin, mode, "-H", host, "-p", port], {
  stdio: "inherit",
  shell: false,
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 0);
});
