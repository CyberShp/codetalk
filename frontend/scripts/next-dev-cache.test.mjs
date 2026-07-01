import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir, rm, writeFile } from "node:fs/promises";
import { test } from "node:test";
import { frontendRoot, nextDevCacheDir, clearNextDevCache } from "./next-dev-cache.mjs";
import { resolve } from "node:path";

test("clearNextDevCache removes only the dev cache directory", async () => {
  const productionBuildMarker = resolve(frontendRoot, ".next", "BUILD_ID");
  const corruptDevCacheFile = resolve(nextDevCacheDir, "cache", "turbopack", "broken.sst");

  await mkdir(resolve(nextDevCacheDir, "cache", "turbopack"), { recursive: true });
  await mkdir(resolve(frontendRoot, ".next"), { recursive: true });
  await writeFile(corruptDevCacheFile, "corrupt turbopack cache\n", "utf8");
  await writeFile(productionBuildMarker, "production-build\n", "utf8");

  await clearNextDevCache({ reason: "test", log: null });

  assert.equal(existsSync(nextDevCacheDir), false);
  assert.equal(existsSync(productionBuildMarker), true);

  await rm(productionBuildMarker, { force: true });
});
