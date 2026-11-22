import { expect, test } from "@playwright/test";
import { clickUntilUrl } from "./helpers";
import { apiData, TOKEN } from "./api";

test("home loads under 3s", async ({ page }) => {
  const start = Date.now();
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  expect(Date.now() - start).toBeLessThan(3000);
});

test("dep graph renders nodes", async ({ page }) => {
  await page.goto("/services/1");
  await expect(page.locator(".react-flow__node").first()).toBeVisible();
});

test("vuln table shows critical badge", async ({ page }) => {
  await page.goto("/vulnerabilities");
  await expect(page.getByRole("table").getByText("CRITICAL", { exact: true }).first()).toBeVisible();
});

test.describe("overview figures match the API", () => {
  test.skip(!TOKEN, "needs DEV_API_TOKEN");

  test("stat cards, severity chart and exposure list", async ({ page, request }) => {
    const stats = await apiData<{ services: number; packages: number; open_vulns: number; critical_vulns: number }>(
      request,
      "/stats/overview",
    );
    await page.goto("/");
    for (const [label, value] of [
      ["Services", stats.services],
      ["Packages", stats.packages],
      ["Open vulns", stats.open_vulns],
      ["Critical vulns", stats.critical_vulns],
    ] as const) {
      const card = page.getByRole("main").getByText(label, { exact: true }).locator("xpath=..");
      await expect(card).toContainText(value.toLocaleString("en-US"));
    }
    await expect(page.getByRole("img", { name: /Critical: \d+, High: \d+/ })).toBeVisible();
    await expect(page.getByRole("link", { name: "api-gateway" })).toBeVisible();
  });
});

test("services list links to a service", async ({ page }) => {
  await page.goto("/services");
  await clickUntilUrl(page, page.getByRole("main").getByRole("link", { name: "api-gateway" }), /\/services\/1$/);
  await expect(page.getByRole("heading", { level: 1, name: "api-gateway" })).toBeVisible();
});
