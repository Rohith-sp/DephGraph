import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: "http://localhost:3000" },
  webServer: {
    command: "npm run start",
    port: 3000,
    reuseExistingServer: true,
    env: { AUTH_DISABLED: "true" },
  },
});
