import { expect, test } from "@playwright/test";

function corsHeaders(origin = "http://localhost:3005") {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Content-Type": "application/json",
  };
}

test("agent workbench renders workflow and task-run controls", async ({ page }) => {
  test.setTimeout(60_000);
  await page.route("**/api/workbench/workflows", async (route) => {
    await route.fulfill({
      json: [],
      headers: corsHeaders(route.request().headers().origin),
    });
  });

  await page.goto("/workbench", { waitUntil: "domcontentloaded", timeout: 60_000 });

  await expect(page.getByRole("heading", { name: "Agent Workbench" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Workflow Registry" })).toBeVisible();
  await expect(page.getByText("codehub-mcp")).toBeVisible();
  await expect(page.getByRole("button", { name: "Prepare run" })).toBeDisabled();
  await expect(page.getByLabel("Repo path")).toBeVisible();
});

test("agent workbench searches semantic cases and evidence memory", async ({ page }) => {
  await page.route("**/api/workbench/workflows", async (route) => {
    await route.fulfill({
      json: [],
      headers: corsHeaders(route.request().headers().origin),
    });
  });
  await page.route("**/api/workbench/semantic-cases/search*", async (route) => {
    await route.fulfill({
      headers: corsHeaders(route.request().headers().origin),
      json: {
        items: [
          {
            semantic_id: "sem_1",
            case_id: "nvme_tcp_tls_handshake_fail",
            feature: "NVMe TCP TLS",
            module: "nvmf_tcp",
            test_level: "black_box",
            scenario: "TLS handshake fails and connection is released",
            terms: ["TLS negotiation"],
            tags: ["resource_cleanup"],
            preconditions: "",
            steps: [],
            expected: "",
            assertion_style: "",
            raw: {},
          },
        ],
      },
    });
  });
  await page.goto("/workbench", { waitUntil: "domcontentloaded" });

  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByText("TLS handshake fails and connection is released")).toBeVisible();
  await expect(page.getByText("Memory facts are structured evidence only")).toBeVisible();
});
