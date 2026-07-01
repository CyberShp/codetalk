import { expect, test } from "@playwright/test";

const jsonHeaders = (origin?: string | null) => ({
  "access-control-allow-origin": origin ?? "*",
  "access-control-allow-headers": "content-type",
  "content-type": "application/json",
});

test("workspace list renders large histories without staggered card animations", async ({ page }) => {
  const workspaces = Array.from({ length: 60 }, (_, index) => ({
    id: `ws-perf-${index}`,
    name: `SPDK validation workspace ${index + 1}`,
    repo_path: `/Volumes/Media/dpdk/spdk-${index + 1}`,
    indexed: 1,
    index_job: null,
    index_progress: 100,
    analyze_status: null,
    analyze_progress: 0,
    last_index_error: null,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
    materials: [],
    reports: [],
  }));

  await page.addInitScript(() => {
    window.localStorage.setItem("codetalk.apiBaseOverride", window.location.origin);
  });
  await page.route("**/api/workspaces**", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: workspaces,
    });
  });
  await page.route("**/api/ai/conversations?**", async (route) => {
    await route.fulfill({
      headers: jsonHeaders(route.request().headers().origin),
      json: { items: [] },
    });
  });

  await page.goto("/workspaces", { waitUntil: "domcontentloaded" });

  await expect(page.locator('a[href="/workspaces/ws-perf-0"]')).toBeVisible();
  await expect(page.locator('a[href="/workspaces/ws-perf-59"]')).toBeAttached();

  const cardMotion = await page.locator(".ct-interactive-card").evaluateAll((nodes) =>
    nodes.map((node) => {
      const element = node as HTMLElement;
      const styles = window.getComputedStyle(element);
      return {
        inlineAnimationDelay: element.style.animationDelay,
        animationName: styles.animationName,
        willChange: styles.willChange,
      };
    }),
  );

  expect(cardMotion).toHaveLength(60);
  expect(
    cardMotion.filter(
      (item) =>
        item.inlineAnimationDelay ||
        item.animationName !== "none" ||
        item.willChange.includes("transform"),
    ),
  ).toEqual([]);
});
