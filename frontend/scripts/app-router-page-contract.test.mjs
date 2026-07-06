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

test("workbench sibling routes import shared experience outside page.tsx", () => {
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
  assert.match(designerSource, /from\s+["']\.\.\/agent-workbench-experience["']/);
  assert.match(semanticSource, /from\s+["']\.\.\/agent-workbench-experience["']/);
});
