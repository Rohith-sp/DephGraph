import { execFileSync } from "node:child_process";
import path from "node:path";

// Deterministic data: seed_db.py truncates every table and reloads the demo set (same as CI).
// Local runs without `python` on PATH: E2E_PYTHON=../api/.venv/bin/python.
export function reseed() {
  execFileSync(process.env.E2E_PYTHON ?? "python", [path.resolve(__dirname, "../../../scripts/seed_db.py")], {
    stdio: "inherit",
  });
}
