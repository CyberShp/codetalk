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

test("clearNextDevCache honors isolated dist dir without touching the shared runtime cache", async () => {
  const previousDistDir = process.env.CODETALK_NEXT_DIST_DIR;
  process.env.CODETALK_NEXT_DIST_DIR = ".next-playwright-e2e";
  const sharedRuntimeCacheFile = resolve(nextDevCacheDir, "server", "runtime-is-still-running.js");
  const isolatedDevCacheDir = resolve(frontendRoot, ".next-playwright-e2e", "dev");
  const isolatedCacheFile = resolve(isolatedDevCacheDir, "server", "old-playwright-cache.js");

  try {
    await mkdir(resolve(nextDevCacheDir, "server"), { recursive: true });
    await mkdir(resolve(isolatedDevCacheDir, "server"), { recursive: true });
    await writeFile(sharedRuntimeCacheFile, "runtime cache must survive\n", "utf8");
    await writeFile(isolatedCacheFile, "isolated cache can be removed\n", "utf8");

    await clearNextDevCache({ reason: "isolated test", log: null });

    assert.equal(existsSync(sharedRuntimeCacheFile), true);
    assert.equal(existsSync(isolatedDevCacheDir), false);
  } finally {
    if (previousDistDir === undefined) {
      delete process.env.CODETALK_NEXT_DIST_DIR;
    } else {
      process.env.CODETALK_NEXT_DIST_DIR = previousDistDir;
    }
    await rm(resolve(frontendRoot, ".next-playwright-e2e"), { recursive: true, force: true });
    await rm(sharedRuntimeCacheFile, { force: true });
  }
});

test("start-playwright-frontend sets an isolated Next dist dir", async () => {
  const source = await import("node:fs/promises").then(({ readFile }) =>
    readFile(new URL("./start-playwright-frontend.mjs", import.meta.url), "utf8"),
  );

  assert.match(source, /CODETALK_NEXT_DIST_DIR/);
  assert.match(source, /nextPlaywrightDistDir/);
  assert.match(source, /\.next-playwright-e2e/);
});
