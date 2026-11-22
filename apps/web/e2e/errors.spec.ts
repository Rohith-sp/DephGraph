import { expect, test } from "@playwright/test";
import { clickUntilUrl, hydrated } from "./helpers";
import { AUTH_DISABLED } from "./credentials";

test("an unknown service id is a 404 page, not a crash", async ({ page }) => {
  await page.goto("/services/999999");
  await expect(page.getByRole("heading", { name: "Not found" })).toBeVisible();
  await clickUntilUrl(page, page.getByRole("link", { name: "Back to services" }), /\/services$/);
});

test("a non-numeric service id is a 404 page", async ({ page }) => {
  await page.goto("/services/abc");
  await expect(page.getByRole("heading", { name: "Not found" })).toBeVisible();
});

test.describe("API down", () => {
  test.skip(!AUTH_DISABLED, "second server runs without auth only in AUTH_DISABLED mode");

  // Port 3100 is the same build with API_URL pointing at a closed port (see playwright.config.ts).
  test("error boundary offers a retry that tries again", async ({ page }) => {
    await page.goto("http://localhost:3100/services");
    const alert = page.locator("main").getByRole("alert");
    await expect(alert).toContainText("Could not load this page");
    const requests: string[] = [];
    page.on("request", (r) => requests.push(r.url()));
    await (await hydrated(alert.getByRole("button", { name: "Try again" }))).click();
    await expect.poll(() => requests.length).toBeGreaterThan(0);
    await expect(alert).toBeVisible();
  });
});
