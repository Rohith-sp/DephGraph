import { execFileSync } from "node:child_process";
import path from "node:path";
import { TEAM2_USER_ID } from "./credentials";

// Deterministic data: seed_db.py truncates every table and reloads the demo set (same as CI).
// Local runs without `python` on PATH: E2E_PYTHON=../api/.venv/bin/python.
export const script = (name: string, ...args: string[]) =>
  execFileSync(process.env.E2E_PYTHON ?? "python", [path.resolve(__dirname, "../../../scripts", name), ...args], {
    stdio: "inherit",
  });

// The dev-token user is seeded into every team; the single-team user is added after the reseed.
export function reseed() {
  script("seed_db.py");
  script("add_team_member.py", "--team-slug", "payments", "--user-id", TEAM2_USER_ID);
}

// Supabase mode: the signed-in e2e users (created through the admin API) join every team.
export const addToAllTeams = (email: string) =>
  script("add_team_member.py", "--team-slug", "platform", "payments", "data", "--email", email, "--role", "admin");
