import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../src/app/workflow-v2.css", import.meta.url), "utf8");

test("run cockpit header can grow instead of covering tabs", () => {
  assert.match(
    css,
    /\.ct-v2-run-cockpit\s*\{[\s\S]*grid-template-rows:\s*minmax\(72px,\s*auto\)\s+auto\s+minmax\(300px,\s*1fr\)/,
  );
  assert.match(css, /\.ct-v2-run-header\s*\{[\s\S]*min-width:\s*0/);
  assert.match(css, /\.ct-v2-run-workspace\s*\{[\s\S]*min-width:\s*0/);
});

test("narrow run cockpit keeps actions out of the tab hit area", () => {
  assert.match(css, /@container\s*\(max-width:\s*680px\)\s*\{[\s\S]*\.ct-v2-run-actions\s*\{[\s\S]*grid-column:\s*1\s*\/\s*-1/);
  assert.match(css, /\.ct-v2-run-actions\s*\{[\s\S]*flex-wrap:\s*wrap/);
});
