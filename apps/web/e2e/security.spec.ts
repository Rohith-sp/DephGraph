import { expect, test } from "@playwright/test";

// A CSP that blocks something Next, React Flow or the charts need shows up as a console error
// ("Refused to ..."), so walk the main pages and require there are none.
test("pages load under the Content-Security-Policy without violations", async ({ page }) => {
  const violations: string[] = [];
  page.on("console", (m) => {
    if (/content security policy|refused to/i.test(m.text())) violations.push(m.text());
  });
  page.on("pageerror", (e) => violations.push(String(e)));
  let csp: string | null = null;
  page.on("response", (r) => {
    if (r.request().isNavigationRequest() && r.url().endsWith("/")) csp = r.headers()["content-security-policy"] ?? null;
  });

  for (const path of ["/", "/services", "/services/1", "/vulnerabilities", "/remediation", "/clusters", "/settings"]) {
    await page.goto(path);
    await expect(page.locator("h1").first()).toBeVisible();
    await page.waitForLoadState("networkidle");
  }
  expect(csp).toContain("frame-ancestors 'none'");
  expect(violations).toEqual([]);
});

test("security headers are sent", async ({ request }) => {
  const h = (await request.get("/login")).headers();
  expect(h["x-frame-options"]).toBe("DENY");
  expect(h["x-content-type-options"]).toBe("nosniff");
  expect(h["referrer-policy"]).toBe("strict-origin-when-cross-origin");
  expect(h["strict-transport-security"]).toContain("max-age=");
  expect(h["permissions-policy"]).toContain("camera=()");
});
