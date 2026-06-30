import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const e2eDir = path.join(process.cwd(), "e2e");

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
