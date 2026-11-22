import { expect, test } from "@playwright/test";
import { clickUntilUrl, hydrated } from "./helpers";

const rows = (page: import("@playwright/test").Page) => page.getByRole("table").getByRole("row").filter({ has: page.getByRole("cell") });

test("severity filter narrows the table and the URL", async ({ page }) => {
  await page.goto("/vulnerabilities");
  await (await hydrated(page.getByLabel("Severity"))).selectOption("CRITICAL");
  await expect(page).toHaveURL(/severity=CRITICAL/);
  await expect(rows(page)).toHaveCount(3);
  for (const cell of await rows(page).locator("td:nth-child(3)").allTextContents()) expect(cell).toBe("CRITICAL");
});

test("search finds one advisory by id", async ({ page }) => {
  await page.goto("/vulnerabilities");
  const search = await hydrated(page.getByLabel("Search"));
  await search.fill("GHSA-seed-0003");
  await search.press("Enter");
  await expect(page).toHaveURL(/q=GHSA-seed-0003/);
  await expect(rows(page)).toHaveCount(1);
  await expect(rows(page).first()).toContainText("GHSA-seed-0003");
});

test("sorting by CVSS flips the order", async ({ page }) => {
  await page.goto("/vulnerabilities");
  const cvss = page.getByRole("columnheader", { name: "CVSS" });
  await expect(cvss).toHaveAttribute("aria-sort", "descending");
  await expect(rows(page).first()).toContainText("10.0");
  await clickUntilUrl(page, cvss.getByRole("link"), /order=asc/);
  await expect(cvss).toHaveAttribute("aria-sort", "ascending");
  await expect(rows(page).first()).toContainText("3.1"); // 2.4 (cargo) affects no production deployment: not listed
});

test("filters, search and sort survive a reload", async ({ page }) => {
  await page.goto("/vulnerabilities?severity=HIGH&ecosystem=npm&q=seeded&sort=published_at&order=asc");
  const first = await rows(page).first().textContent();
  await page.reload();
  await expect(page.getByLabel("Severity")).toHaveValue("HIGH");
  await expect(page.getByLabel("Ecosystem")).toHaveValue("npm");
  await expect(page.getByLabel("Search")).toHaveValue("seeded");
  await expect(page.getByRole("columnheader", { name: "Published" })).toHaveAttribute("aria-sort", "ascending");
  await expect(rows(page).first()).toHaveText(first!);
  await expect(page).toHaveURL(/severity=HIGH.*ecosystem=npm|ecosystem=npm.*severity=HIGH/);
});

test("unknown filter values are ignored, not an error", async ({ page }) => {
  await page.goto("/vulnerabilities?severity=bogus&ecosystem=nope");
  await expect(page.getByRole("heading", { name: "Vulnerabilities" })).toBeVisible();
  await expect(page.locator("main").getByRole("alert")).toHaveCount(0);
  await expect(rows(page).first()).toBeVisible();
});

test("a search with no match shows an empty state", async ({ page }) => {
  await page.goto("/vulnerabilities?q=zzzz-no-such-advisory");
  await expect(page.getByText("No vulnerabilities match.")).toBeVisible();
});
