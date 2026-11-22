import { expect, test } from "@playwright/test";
import { AUTH_DISABLED } from "./credentials";

// Port 3200 is the same build as a user who belongs to the Payments team (team 2) only.
const TEAM2 = "http://localhost:3200";

test.describe("single-team user", () => {
  test.skip(!AUTH_DISABLED, "the single-team server runs only in AUTH_DISABLED mode");

  test("services list shows only that team's services", async ({ page }) => {
    await page.goto(`${TEAM2}/services`);
    await expect(page.getByRole("link", { name: "checkout-web" })).toBeVisible();
    await expect(page.getByRole("link", { name: "payments-worker" })).toBeVisible();
    await expect(page.getByRole("row")).toHaveCount(3); // header + two services
    for (const other of ["api-gateway", "auth-service", "analytics-dashboard"]) {
      await expect(page.getByRole("link", { name: other })).toHaveCount(0);
    }
  });

  test("another team's service is the 404 page", async ({ page }) => {
    await page.goto(`${TEAM2}/services/1`);
    await expect(page.getByRole("heading", { name: "Not found" })).toBeVisible();
  });

  test("overview counts only the team's services", async ({ page }) => {
    await page.goto(`${TEAM2}/`);
    await expect(page.getByRole("main").getByText("Services", { exact: true }).locator("..")).toContainText("2");
  });

  test("remediation offers only the member team", async ({ page }) => {
    await page.goto(`${TEAM2}/remediation?team_id=1`); // a foreign team id falls back to the own team
    await expect(page.getByLabel("Team").getByRole("option")).toHaveText(["Payments"]);
  });

  test("settings lists the user's teams and roles", async ({ page }) => {
    await page.goto(`${TEAM2}/settings`);
    const row = page.getByRole("row", { name: /Payments/ });
    await expect(row).toContainText("member");
    await expect(page.getByRole("row", { name: /Platform/ })).toHaveCount(0);
  });
});

test("the dev user sees every team", async ({ page }) => {
  test.skip(!AUTH_DISABLED, "dev token only");
  await page.goto("/settings");
  for (const team of ["Platform", "Payments", "Data"]) {
    await expect(page.getByRole("row", { name: new RegExp(team) })).toContainText("admin");
  }
});
