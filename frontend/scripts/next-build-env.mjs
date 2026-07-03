import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { buildFrontendRuntimeEnv } from "./frontend-runtime-env.mjs";

const require = createRequire(import.meta.url);
const nextBin = require.resolve("next/dist/bin/next");

const child = spawn(process.execPath, [nextBin, "build"], {
  env: buildFrontendRuntimeEnv(process.env),
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
