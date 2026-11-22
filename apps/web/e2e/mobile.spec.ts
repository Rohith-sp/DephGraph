import { expect, test } from "@playwright/test";

test.use({ viewport: { width: 400, height: 800 } });

const ROUTES = ["/", "/services", "/services/1", "/vulnerabilities", "/remediation", "/clusters", "/settings"];

for (const route of ROUTES) {
  test(`no horizontal scroll at 400px: ${route}`, async ({ page }) => {
    await page.goto(route);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
}

for (const route of ["/login", "/register"]) {
  test(`no horizontal scroll at 400px: ${route}`, async ({ page }) => {
    await page.goto(route);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  });
}
