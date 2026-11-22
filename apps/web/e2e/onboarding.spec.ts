import { expect, test } from "@playwright/test";
import { hydrated } from "./helpers";

test("create a team, then add a service to it", async ({ page }) => {
  await page.goto("/settings");
  await (await hydrated(page.getByLabel("Team name"))).fill("Onboarding Squad");
  await page.getByRole("button", { name: "Create team" }).click();
  await expect(page.getByRole("row", { name: /Onboarding Squad/ })).toContainText("admin");

  await page.goto("/services");
  await (await hydrated(page.getByLabel("GitHub repository URL"))).fill("https://github.com/Acme/Onboarding-Demo.git");
  await page.locator("select[name=team]").selectOption({ label: "Onboarding Squad" });
  await page.getByRole("button", { name: "Add service" }).click();
  await expect(page.getByRole("link", { name: "onboarding-demo" })).toBeVisible();
});
