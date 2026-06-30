import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

test("SPDK real E2E writes a dedicated failure report artifact", () => {
  const spec = fs.readFileSync(path.join(process.cwd(), "e2e/spdk-real-e2e.spec.ts"), "utf8");

  expect(spec).toContain("function writeAcceptanceReports()");
  expect(spec).toContain('"failure_report.json"');
  expect(spec).toContain('"failure_report.md"');
  expect(spec).toContain('"artifact_manifest.json"');
  expect(spec).toContain("function writeArtifactManifest");
  expect(spec).toContain("function fileSha256");
  expect(spec).toContain("fs.readSync");
  expect(spec).toContain("function redactReportText");
  expect(spec).toContain("function sanitizeCaseForReport");
  expect(spec).toContain("function textArtifactPatternLeaks");
  expect(spec).toContain("function fileContainsPattern");
  expect(spec).toContain("function verifyStreamingSecretScanner");
  expect(spec).not.toContain("stat.size > 2_000_000");
  expect(spec).toContain("total_problem_cases");
  expect(spec).toContain("productized: problemCases.length === 0");
});
