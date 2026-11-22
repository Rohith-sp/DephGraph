import { defineConfig } from "@playwright/test";
import { AUTH_DISABLED, STATE } from "./e2e/credentials";

// AUTH_DISABLED=true (+ DEV_API_TOKEN) is the CI mode; otherwise real Supabase auth.
const env = { AUTH_DISABLED: process.env.AUTH_DISABLED ?? "", DEV_API_TOKEN: process.env.DEV_API_TOKEN ?? "" };

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  // One worker: specs share one database and some of them write to it.
  workers: 1,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? "github" : "list",
  use: { baseURL: "http://localhost:3000", storageState: AUTH_DISABLED ? undefined : STATE },
  webServer: [
    { command: "npm run start", port: 3000, reuseExistingServer: !process.env.CI, env },
    // Same build pointed at a closed port: the "API is down" case in errors.spec.ts.
    {
      command: "npx next start -p 3100",
      port: 3100,
      reuseExistingServer: !process.env.CI,
      env: { ...env, API_URL: "http://127.0.0.1:9" },
    },
  ],
});
