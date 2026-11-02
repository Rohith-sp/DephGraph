import { expect, test } from "@playwright/test";
import { AUTH_DISABLED, EMAIL, PASSWORD, SIGNOUT_EMAIL } from "./credentials";

test.skip(AUTH_DISABLED, "auth is bypassed in AUTH_DISABLED mode");
test.use({ storageState: { cookies: [], origins: [] } });

test("unauthenticated visit redirects to /login", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});

test("wrong password shows an error", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByText(/invalid login credentials/i)).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);
});

test("login shows dashboard data, sign-out returns to /login", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(SIGNOUT_EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL((u) => u.pathname === "/");
  await page.goto("/services");
  await expect(page.getByText("api-gateway").first()).toBeVisible();

  await page.getByRole("button", { name: /sign out/i }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
});
