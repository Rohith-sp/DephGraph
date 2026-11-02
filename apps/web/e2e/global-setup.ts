import { chromium, type FullConfig } from "@playwright/test";
import { AUTH_DISABLED, EMAIL, PASSWORD, SIGNOUT_EMAIL, STATE } from "./credentials";

// Signs in through the real /login UI once and saves the session for every test.
export default async function globalSetup(config: FullConfig) {
  if (AUTH_DISABLED) return;
  const supabase = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const admin = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (supabase && admin) {
    // Idempotent: 422 "already registered" is fine.
    for (const email of [EMAIL, SIGNOUT_EMAIL]) {
      await fetch(`${supabase}/auth/v1/admin/users`, {
        method: "POST",
        headers: { apikey: admin, Authorization: `Bearer ${admin}`, "Content-Type": "application/json" },
        body: JSON.stringify({ email, password: PASSWORD, email_confirm: true }),
      });
    }
  }
  const browser = await chromium.launch();
  const page = await browser.newPage({ baseURL: config.projects[0].use.baseURL });
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.waitForURL((u) => u.pathname === "/");
  await page.context().storageState({ path: STATE });
  await browser.close();
}
