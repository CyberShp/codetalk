import { expect, test } from "@playwright/test";

test("shared shell uses the restrained engineering-console visual language", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/tasks", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".ct-v2-table-shell")).toBeVisible();

  const metrics = await page.evaluate(() => {
    const sidebar = document.querySelector<HTMLElement>(".ct-app-sidebar");
    const mark = document.querySelector<HTMLElement>(".ct-app-sidebar__mark");
    const heading = document.querySelector<HTMLElement>(".ct-v2-page-header h1");
    const table = document.querySelector<HTMLElement>(".ct-v2-table-shell");
    if (!sidebar || !mark || !heading || !table) return null;
    const sidebarStyle = getComputedStyle(sidebar);
    const markStyle = getComputedStyle(mark);
    const headingStyle = getComputedStyle(heading);
    const tableStyle = getComputedStyle(table);
    return {
      bodyBackgroundImage: getComputedStyle(document.body).backgroundImage,
      sidebarBackgroundImage: sidebarStyle.backgroundImage,
      sidebarBackdropFilter: sidebarStyle.backdropFilter,
      sidebarShadow: sidebarStyle.boxShadow,
      markRadius: Number.parseFloat(markStyle.borderRadius),
      headingSize: Number.parseFloat(headingStyle.fontSize),
      tableRadius: Number.parseFloat(tableStyle.borderRadius),
    };
  });

  expect(metrics).not.toBeNull();
  expect(metrics!.bodyBackgroundImage).toBe("none");
  expect(metrics!.sidebarBackgroundImage).toBe("none");
  expect(metrics!.sidebarBackdropFilter).toBe("none");
  expect(metrics!.sidebarShadow).toBe("none");
  expect(metrics!.markRadius).toBeLessThanOrEqual(8);
  expect(metrics!.headingSize).toBeLessThanOrEqual(20);
  expect(metrics!.tableRadius).toBeLessThanOrEqual(8);
});

test("AI pages use compact workspace typography instead of hero-card styling", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/ai", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".ct-ai-home__projects")).toBeVisible();

  const metrics = await page.locator(".ct-ai-home__header").evaluate((header) => {
    const title = header.querySelector<HTMLElement>("h1");
    const paragraph = header.querySelector<HTMLElement>("p");
    const styles = getComputedStyle(header);
    return {
      radius: Number.parseFloat(styles.borderRadius),
      shadow: styles.boxShadow,
      backgroundImage: styles.backgroundImage,
      titleSize: title ? Number.parseFloat(getComputedStyle(title).fontSize) : 999,
      bodySize: paragraph ? Number.parseFloat(getComputedStyle(paragraph).fontSize) : 999,
    };
  });

  expect(metrics.radius).toBeLessThanOrEqual(8);
  expect(metrics.shadow).toBe("none");
  expect(metrics.backgroundImage).toBe("none");
  expect(metrics.titleSize).toBeLessThanOrEqual(22);
  expect(metrics.bodySize).toBeLessThanOrEqual(13);

  const panels = await page.locator(".ct-ai-home__projects, .ct-ai-home__threads").evaluateAll((nodes) =>
    nodes.map((node) => {
      const style = getComputedStyle(node);
      return {
        radius: Number.parseFloat(style.borderRadius),
        shadow: style.boxShadow,
      };
    }),
  );
  expect(panels.length).toBeGreaterThan(0);
  for (const panel of panels) {
    expect(panel.radius).toBeLessThanOrEqual(8);
    expect(panel.shadow).toBe("none");
  }
});

test("AI hub remains a single bounded column on a phone viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/ai", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".ct-ai-home__projects")).toBeVisible();

  const metrics = await page.evaluate(() => {
    const grid = document.querySelector<HTMLElement>(".ct-ai-home__grid");
    const home = document.querySelector<HTMLElement>(".ct-ai-home");
    const shell = document.querySelector<HTMLElement>(".ct-page-shell");
    const threads = document.querySelector<HTMLElement>(".ct-ai-home__threads");
    const panels = [...document.querySelectorAll<HTMLElement>(
      ".ct-ai-home__projects, .ct-ai-home__threads",
    )];
    const homeRect = home?.getBoundingClientRect();
    const shellRect = shell?.getBoundingClientRect();
    const threadsRect = threads?.getBoundingClientRect();
    return {
      viewportWidth: window.innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      gridColumns: grid ? getComputedStyle(grid).gridTemplateColumns : "",
      homeBottom: homeRect?.bottom ?? Number.POSITIVE_INFINITY,
      shellBottom: shellRect?.bottom ?? 0,
      threadsBottom: threadsRect?.bottom ?? Number.POSITIVE_INFINITY,
      panels: panels.map((panel) => {
        const rect = panel.getBoundingClientRect();
        return { left: rect.left, right: rect.right, width: rect.width };
      }),
    };
  });

  expect(metrics.documentWidth).toBeLessThanOrEqual(metrics.viewportWidth + 1);
  expect(metrics.gridColumns.trim().split(/\s+/)).toHaveLength(1);
  expect(metrics.homeBottom).toBeLessThanOrEqual(metrics.shellBottom + 1);
  expect(metrics.threadsBottom).toBeLessThanOrEqual(metrics.homeBottom + 1);
  for (const panel of metrics.panels) {
    expect(panel.left).toBeGreaterThanOrEqual(0);
    expect(panel.right).toBeLessThanOrEqual(metrics.viewportWidth + 1);
  }
});

test("task table clips long workflow and workspace names inside stable columns", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/tasks", { waitUntil: "domcontentloaded" });
  const row = page.locator(".ct-v2-task-center .ct-v2-table tbody tr").first();
  await expect(row).toBeVisible();

  const metrics = await row.evaluate((element) => {
    const cells = [...element.querySelectorAll<HTMLElement>("td")];
    const workflowName = cells[3]?.querySelector<HTMLElement>("strong");
    const workspaceName = cells[4];
    const styleOf = (node?: HTMLElement | null) => node ? {
      overflow: getComputedStyle(node).overflow,
      textOverflow: getComputedStyle(node).textOverflow,
      whiteSpace: getComputedStyle(node).whiteSpace,
    } : null;
    return {
      workflow: styleOf(workflowName),
      workspace: styleOf(workspaceName),
    };
  });

  expect(metrics.workflow).toEqual({
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  });
  expect(metrics.workspace).toEqual({
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  });
});
