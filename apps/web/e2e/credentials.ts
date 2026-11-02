export const EMAIL = process.env.E2E_EMAIL ?? "demo@dephgraph.local";
export const PASSWORD = process.env.E2E_PASSWORD ?? "demo-password-1";
// CI without Supabase: AUTH_DISABLED=true skips login (see README "Run locally").
export const AUTH_DISABLED = process.env.AUTH_DISABLED === "true";
export const STATE = "e2e/.auth/state.json";
// Own user for the sign-out test: Supabase sign-out revokes every session of that user.
export const SIGNOUT_EMAIL = "e2e-signout@dephgraph.local";
