import { expect, test } from "@playwright/test";
import { hydrated } from "./helpers";

test("the sidebar is reachable by keyboard with a visible focus ring", async ({ page }) => {
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");

  for (const name of ["Overview", "Services", "Vulnerabilities", "Remediation", "Clusters", "Settings"]) {
    await page.keyboard.press("Tab");
    const link = nav.getByRole("link", { name });
    await expect(link).toBeFocused();
    await expect(link).toHaveCSS("outline-style", "solid");
    await expect(link).toHaveCSS("outline-width", "2px");
  }

  await (await hydrated(nav.getByRole("link", { name: "Services" }))).focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/services$/);
  await expect(nav.getByRole("link", { name: "Services" })).toHaveAttribute("aria-current", "page");
});

test("filters are operable without a mouse", async ({ page }) => {
  await page.goto("/vulnerabilities");
  await (await hydrated(page.getByLabel("Severity"))).focus();
  await page.keyboard.press("ArrowDown"); // All -> Critical
  await expect(page).toHaveURL(/severity=CRITICAL/);
});
