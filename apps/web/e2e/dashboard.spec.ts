import { expect, test } from "@playwright/test";

test("home loads under 3s", async ({ page }) => {
  const start = Date.now();
  await page.goto("/");
  await expect(page.getByText("Services")).toBeVisible();
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
