import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

test("SPDK real E2E writes a dedicated failure report artifact", () => {
  const spec = fs.readFileSync(path.join(process.cwd(), "e2e/spdk-real-e2e.spec.ts"), "utf8");

  expect(spec).toContain("function writeAcceptanceReports()");
  expect(spec).toContain('"failure_report.json"');
  expect(spec).toContain('"failure_report.md"');
  expect(spec).toContain("function redactReportText");
  expect(spec).toContain("function sanitizeCaseForReport");
  expect(spec).toContain("total_problem_cases");
  expect(spec).toContain("productized: problemCases.length === 0");
});
