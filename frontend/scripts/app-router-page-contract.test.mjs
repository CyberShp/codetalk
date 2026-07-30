import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const allowedNamedExports = new Set([
  "config",
  "dynamic",
  "dynamicParams",
  "experimental_ppr",
  "fetchCache",
  "generateImageMetadata",
  "generateMetadata",
  "generateSitemaps",
  "generateStaticParams",
  "maxDuration",
  "metadata",
  "preferredRegion",
  "revalidate",
  "runtime",
  "viewport",
]);

function namedFunctionExports(source) {
  return Array.from(source.matchAll(/export\s+(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(/g)).map(
    (match) => match[1],
  );
}

test("workbench page module only exports App Router page-safe fields", () => {
  const pageSource = readFileSync(new URL("../src/app/workbench/page.tsx", import.meta.url), "utf8");
  const illegalExports = namedFunctionExports(pageSource).filter((name) => !allowedNamedExports.has(name));

  assert.deepEqual(illegalExports, []);
});

test("workbench sibling routes use the release gate without importing a page module", () => {
  const designerSource = readFileSync(
    new URL("../src/app/workbench/designer/page.tsx", import.meta.url),
    "utf8",
  );
  const semanticSource = readFileSync(
    new URL("../src/app/workbench/semantic/page.tsx", import.meta.url),
    "utf8",
  );

  assert.doesNotMatch(designerSource, /from\s+["']\.\.\/page["']/);
  assert.doesNotMatch(semanticSource, /from\s+["']\.\.\/page["']/);
  assert.match(designerSource, /WorkbenchEntryGate/);
  assert.match(semanticSource, /WorkbenchEntryGate/);
  assert.match(designerSource, /destination="\/workflows"/);
  assert.match(semanticSource, /destination="\/semantic-library"/);
});

test("workspace creation keeps the optional local folder browser wired", () => {
  const pageSource = readFileSync(
    new URL("../src/app/workspaces/new/page.tsx", import.meta.url),
    "utf8",
  );
  const apiSource = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");

  assert.match(pageSource, /本地文件夹路径[\s\S]{0,80}可选/);
  assert.match(pageSource, /浏览/);
  assert.match(pageSource, /api\.workspaces\.browseFolders/);
  assert.match(pageSource, /repo_path:\s*submittedRepoPath/);
  assert.match(apiSource, /browseFolders/);
  assert.match(apiSource, /\/api\/workspaces\/folders/);
});
