import { rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptsDir = dirname(fileURLToPath(import.meta.url));
export const frontendRoot = resolve(scriptsDir, "..");

export function nextDistDir(env = process.env) {
  return resolve(frontendRoot, env.CODETALK_NEXT_DIST_DIR || ".next");
}

export function nextDevCacheDirForEnv(env = process.env) {
  return resolve(nextDistDir(env), "dev");
}

export const nextDevCacheDir = nextDevCacheDirForEnv();

export async function clearNextDevCache({ reason = "before next dev", log = console.log } = {}) {
  const target = nextDevCacheDirForEnv();
  await rm(target, { recursive: true, force: true });
  if (log) {
    log(`Cleared Next.js dev cache at ${target} (${reason}).`);
  }
}
