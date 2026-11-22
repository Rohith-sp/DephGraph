export const EMAIL = process.env.E2E_EMAIL ?? "demo@dephgraph.local";
export const PASSWORD = process.env.E2E_PASSWORD ?? "demo-password-1";
// CI without Supabase: AUTH_DISABLED=true skips login (see README "Run locally").
export const AUTH_DISABLED = process.env.AUTH_DISABLED === "true";
export const STATE = "e2e/.auth/state.json";
// Own user for the sign-out test: Supabase sign-out revokes every session of that user.
export const SIGNOUT_EMAIL = "e2e-signout@dephgraph.local";
// AUTH_DISABLED mode only: a user who belongs to the Payments team (team 2) and nothing else. A third
// web server (port 3200, see playwright.config.ts) talks to the API with this user's token.
export const TEAM2_USER_ID = "00000000-0000-0000-0000-000000000002";
