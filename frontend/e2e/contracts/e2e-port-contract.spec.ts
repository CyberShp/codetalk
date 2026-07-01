import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const e2eDir = path.join(process.cwd(), "e2e");
const repoRoot = path.resolve(process.cwd(), "..");

function readE2eFiles(dir: string): Array<{ file: string; content: string }> {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      return readE2eFiles(fullPath);
    }
    if (!entry.isFile() || !entry.name.endsWith(".ts")) {
      return [];
    }
    return [{ file: path.relative(process.cwd(), fullPath), content: fs.readFileSync(fullPath, "utf8") }];
  });
}

test("E2E helpers derive frontend origin from CODETALK_FRONTEND_PORT", () => {
  const files = readE2eFiles(e2eDir);
  const offenders = files
    .filter(({ file }) => file !== "e2e/contracts/e2e-port-contract.spec.ts")
    .filter(({ content }) => content.includes("http://localhost:3003") || content.includes("http://127.0.0.1:3003"))
    .map(({ file }) => file);

  expect(offenders, "E2E code should not hardcode the default frontend port").toEqual([]);
});

test("release and diagnostic entrypoints use the public 3003 frontend default", () => {
  const files = [
    path.join(process.cwd(), "release-e2e", "release-clickthrough.spec.ts"),
    path.join(repoRoot, "scripts", "coverage_real_e2e.py"),
    path.join(repoRoot, "docs", "INTERNAL_RELEASE.md"),
  ];
  const offenders = files
    .map((file) => ({ file: path.relative(repoRoot, file), content: fs.readFileSync(file, "utf8") }))
    .filter(({ content }) => content.includes("3205") || content.includes("CODETALK_FRONTEND_PORT ?? \"3205\""))
    .map(({ file }) => file);

  expect(offenders, "release/diagnostic flows must not default to the retired 3205 frontend port").toEqual([]);
});
