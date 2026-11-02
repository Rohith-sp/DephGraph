import { defineConfig } from "@playwright/test";
import { AUTH_DISABLED, STATE } from "./e2e/credentials";

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  use: { baseURL: "http://localhost:3000", storageState: AUTH_DISABLED ? undefined : STATE },
  webServer: {
    command: "npm run start",
    port: 3000,
    reuseExistingServer: true,
    // AUTH_DISABLED=true (+ DEV_API_TOKEN) is the CI mode; otherwise real Supabase auth.
    env: { AUTH_DISABLED: process.env.AUTH_DISABLED ?? "", DEV_API_TOKEN: process.env.DEV_API_TOKEN ?? "" },
  },
});
