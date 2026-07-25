import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

test("legacy Workbench entries always redirect to the versioned destinations", async ({ page }) => {
  for (const [legacy, destination] of [
    ["/workbench", "/tasks"],
    ["/workbench/designer", "/workflows"],
    ["/workbench/semantic", "/semantic-library"],
  ] as const) {
    await page.goto(legacy, { waitUntil: "domcontentloaded" });
    await expect(page).toHaveURL(new RegExp(`${destination.replace("/", "\\/")}$`));
  }
});

test("V2 libraries fit 1440, 1280, and 1024 without page overflow or double main scrolling", async ({ page }) => {
  const evidenceDir = path.join(process.cwd(), "output", "playwright", "phase8");
  fs.mkdirSync(evidenceDir, { recursive: true });
  for (const width of [1440, 1280, 1024]) {
    await page.setViewportSize({ width, height: 900 });
    for (const route of ["/tasks", "/workflows", "/semantic-library", "/evidence-library"]) {
      await page.goto(route, { waitUntil: "domcontentloaded" });
      await expect(page.locator("main").last()).toBeVisible();
      const layout = await page.evaluate(() => {
        const shell = document.querySelector(".ct-page-shell");
        const shellStyle = shell ? getComputedStyle(shell) : null;
        return {
          viewport: window.innerWidth,
          bodyWidth: document.documentElement.scrollWidth,
          shellOverflowY: shellStyle?.overflowY || "visible",
        };
      });
      expect(layout.bodyWidth).toBeLessThanOrEqual(layout.viewport + 1);
      expect(["auto", "scroll"]).not.toContain(layout.shellOverflowY);
    }
    await page.goto("/tasks", { waitUntil: "domcontentloaded" });
    await page.screenshot({
      path: path.join(evidenceDir, `tasks-${width}x900.png`),
      fullPage: false,
    });
  }
});

test("primary V2 text meets the normal-text WCAG AA contrast threshold", async ({ page }) => {
  await page.goto("/tasks", { waitUntil: "domcontentloaded" });
  const contrast = await page.locator(".ct-v2-page-header p").evaluate((element) => {
    const parse = (value: string) => value.match(/[\d.]+/g)?.slice(0, 3).map(Number) ?? [0, 0, 0];
    const channel = (value: number) => {
      const normalized = value / 255;
      return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
    };
    const luminance = (rgb: number[]) => 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
    const color = parse(getComputedStyle(element).color);
    let parent: Element | null = element;
    let background = [255, 255, 255];
    while (parent) {
      const value = getComputedStyle(parent).backgroundColor;
      const rgba = value.match(/[\d.]+/g)?.map(Number) ?? [];
      if (rgba.length >= 3 && (rgba[3] === undefined || rgba[3] > 0)) {
        background = rgba.slice(0, 3);
        break;
      }
      parent = parent.parentElement;
    }
    const lighter = Math.max(luminance(color), luminance(background));
    const darker = Math.min(luminance(color), luminance(background));
    return (lighter + 0.05) / (darker + 0.05);
  });
  expect(contrast).toBeGreaterThanOrEqual(4.5);
});
