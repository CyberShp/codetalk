import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

test("mermaid diagrams follow the bright report theme contract", async () => {
  const source = fs.readFileSync(
    path.join(process.cwd(), "src/components/ui/MermaidRenderer.tsx"),
    "utf8",
  );

  expect(source).not.toContain('theme: "dark"');
  expect(source).not.toContain("darkMode: true");
  expect(source).toContain('theme: "base"');
  expect(source).toContain("background: \"#FFFFFF\"");
});
