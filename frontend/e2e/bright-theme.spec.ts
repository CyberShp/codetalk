import { test, expect } from "@playwright/test";

function luminance(rgb: string): number {
  const match = rgb.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  const srgbMatch = rgb.match(/color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)/);
  if (!match && !srgbMatch) return 0;
  const [, r, g, b] = match
    ? match.map(Number)
    : [0, ...srgbMatch!.slice(1).map((value) => Number(value) * 255)];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

test("global app shell uses a bright professional testing theme", async ({ page }) => {
  await page.route("http://localhost:8100/api/workspaces", async (route) => {
    await route.fulfill({ json: [] });
  });
  await page.route("http://localhost:8100/api/deepwiki/repos", async (route) => {
    await route.fulfill({ json: [] });
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "CODETALK", exact: true })).toBeVisible();

  const colors = await page.evaluate(() => {
    const body = getComputedStyle(document.body);
    const sidebar = getComputedStyle(document.querySelector("aside")!);
    const heading = getComputedStyle(document.querySelector("h1")!);
    return {
      bodyBg: body.backgroundColor,
      bodyText: body.color,
      sidebarBg: sidebar.backgroundColor,
      headingText: heading.color,
    };
  });

  expect(luminance(colors.bodyBg)).toBeGreaterThan(230);
  expect(luminance(colors.sidebarBg)).toBeGreaterThan(240);
  expect(luminance(colors.bodyText)).toBeLessThan(80);
  expect(luminance(colors.headingText)).toBeLessThan(130);
});
