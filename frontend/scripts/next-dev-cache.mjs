import { rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptsDir = dirname(fileURLToPath(import.meta.url));
export const frontendRoot = resolve(scriptsDir, "..");
export const nextDevCacheDir = resolve(frontendRoot, ".next", "dev");

export async function clearNextDevCache({ reason = "before next dev", log = console.log } = {}) {
  await rm(nextDevCacheDir, { recursive: true, force: true });
  if (log) {
    log(`Cleared Next.js dev cache at ${nextDevCacheDir} (${reason}).`);
  }
}
